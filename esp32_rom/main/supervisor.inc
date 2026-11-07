/* Resident supervisor. Included by service.c after UART reservation helpers.
 * No task deletion/cancellation: synchronous queue submitters retain every
 * borrowed pointer until completion or whole-chip reset. No memory isolation. */
#include "esp_attr.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "module_runtime.h"
#define SUPERVISOR_PORT 3337
#include "rmb/supervisor.h"
#define SUPERVISOR_JOB_US (1800LL * 1000000)
typedef rmb_supervisor_record supervisor_record;
static RTC_NOINIT_ATTR supervisor_record supervisor_rtc;
static portMUX_TYPE supervisor_lock = portMUX_INITIALIZER_UNLOCKED;
static bool supervisor_fault, supervisor_active, supervisor_updating, supervisor_reboot_required;
/* Boot-only execution permission; NEVER recomputed from current ready/footer. */
static rmb_supervisor_entry supervisor_entry;
#define module_allowed_this_boot (supervisor_entry.allowed)
static uint32_t supervisor_stage, supervisor_uploaded;
static int64_t supervisor_started, supervisor_last_progress, supervisor_limit_us;
static TaskHandle_t supervisor_worker_handle;
static QueueHandle_t supervisor_queue;
typedef enum { SJ_INIT, SJ_VALIDATE, SJ_DOWNLOAD, SJ_PROBE, SJ_RESTART, SJ_HANG } supervisor_kind;
typedef struct {
 supervisor_kind kind; const rom_io *io; const rom_source *source;
 uint32_t size, crc; int ram; rom_probe_result *probe;
 rom_trace_fn trace; void *trace_ctx; SemaphoreHandle_t done; int result;
} supervisor_job;
static void supervisor_record_write_locked(void) {
 rmb_supervisor_record_write(&supervisor_rtc,supervisor_fault,supervisor_active,supervisor_stage);
}
static void supervisor_progress(void) {
 if(xTaskGetCurrentTaskHandle()!=supervisor_worker_handle) return;
 portENTER_CRITICAL(&supervisor_lock);
 supervisor_last_progress=esp_timer_get_time();
 ++supervisor_stage; /* Monotonic callback sequence, not untrusted module text. */
 supervisor_record_write_locked();
 portEXIT_CRITICAL(&supervisor_lock);
 (void)esp_task_wdt_reset();
}
static void supervisor_worker(void *arg) {
 (void)arg;
 ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
 for(;;) {
  supervisor_job *job=NULL;
  (void)esp_task_wdt_reset();
  if(xQueueReceive(supervisor_queue,&job,pdMS_TO_TICKS(250))!=pdTRUE) continue;
  portENTER_CRITICAL(&supervisor_lock);
  supervisor_active=true; supervisor_stage=0;
  supervisor_started=supervisor_last_progress=esp_timer_get_time();
  supervisor_limit_us=job->kind==SJ_DOWNLOAD?SUPERVISOR_JOB_US:
                      job->kind==SJ_RESTART?10000000LL:60000000LL;
  if(job->kind==SJ_HANG) supervisor_fault=true;
  supervisor_record_write_locked();
  portEXIT_CRITICAL(&supervisor_lock);
  switch(job->kind) {
   case SJ_INIT: job->result=module_runtime_init(); break;
   case SJ_VALIDATE: job->result=module_rom_validate(device,job->source,job->size,job->crc,job->ram); break;
   case SJ_DOWNLOAD: job->result=module_rom_download_traced(job->io,device,job->source,job->size,job->crc,job->ram,job->trace,job->trace_ctx); break;
   case SJ_PROBE: job->result=module_rom_probe(job->io,device,job->probe,job->trace,job->trace_ctx); break;
   case SJ_RESTART: job->result=module_rom_restart(job->io); break;
   case SJ_HANG:
    /* Yield CPU to network/idle but NEVER feed this worker's TWDT subscription. */
    for(;;) vTaskDelay(1);
  }
  portENTER_CRITICAL(&supervisor_lock);
  supervisor_active=false; supervisor_record_write_locked();
  portEXIT_CRITICAL(&supervisor_lock);
  (void)esp_task_wdt_reset();
  if(job->done) xSemaphoreGive(job->done);
 }
}
static int supervisor_submit(supervisor_job *job) {
 if(!supervisor_queue || (supervisor_fault && job->kind!=SJ_INIT)) return ROM_E_CAP;
 job->done=xSemaphoreCreateBinary();
 if(!job->done) return ROM_E_IO;
 if(xQueueSend(supervisor_queue,&job,0)!=pdTRUE) { vSemaphoreDelete(job->done); return ROM_E_IO; }
 /* Never time out and return borrowed stack/I/O pointers to a live worker. */
 xSemaphoreTake(job->done,portMAX_DELAY);
 vSemaphoreDelete(job->done); return job->result;
}
static int supervisor_validate(const rom_device *d,const rom_source *s,uint32_t n,uint32_t crc,int ram) {
 (void)d; supervisor_job j={.kind=SJ_VALIDATE,.source=s,.size=n,.crc=crc,.ram=ram}; return supervisor_submit(&j);
}
static int supervisor_download(const rom_io *io,const rom_device *d,const rom_source *s,uint32_t n,uint32_t crc,int ram,rom_trace_fn trace,void *ctx) {
 (void)d; supervisor_job j={.kind=SJ_DOWNLOAD,.io=io,.source=s,.size=n,.crc=crc,.ram=ram,.trace=trace,.trace_ctx=ctx}; return supervisor_submit(&j);
}
static int supervisor_probe(const rom_io *io,const rom_device *d,rom_probe_result *p,rom_trace_fn trace,void *ctx) {
 (void)d; supervisor_job j={.kind=SJ_PROBE,.io=io,.probe=p,.trace=trace,.trace_ctx=ctx}; return supervisor_submit(&j);
}
static int supervisor_restart(const rom_io *io) {
 supervisor_job j={.kind=SJ_RESTART,.io=io}; return supervisor_submit(&j);
}
/* INIT may never return. Never lend a manager/app_main stack or wait for it.
 * One entry invocation per boot; replacement activation requires ESP restart. */
#define supervisor_init_submitted (supervisor_entry.submitted)
static int supervisor_start(void) {
 static supervisor_job init_job;
 if(!rmb_supervisor_entry_can_start(&supervisor_entry) || !supervisor_queue) return ROM_E_CAP;
 init_job=(supervisor_job){.kind=SJ_INIT};
 supervisor_job *pending=&init_job;
 portENTER_CRITICAL(&supervisor_lock);
 supervisor_active=true; supervisor_stage=0;
 supervisor_started=supervisor_last_progress=esp_timer_get_time();
 supervisor_limit_us=60000000LL;
 supervisor_record_write_locked();
 portEXIT_CRITICAL(&supervisor_lock);
 if(xQueueSend(supervisor_queue,&pending,0)!=pdTRUE) {
  portENTER_CRITICAL(&supervisor_lock);
  supervisor_active=false; supervisor_record_write_locked();
  portEXIT_CRITICAL(&supervisor_lock);
  return ROM_E_IO;
 }
 rmb_supervisor_entry_submitted(&supervisor_entry);
 return 0;
}
static void supervisor_status(int fd) {
 char out[640]; bool active,fault,updating; uint32_t stage,uploaded; int64_t elapsed,progress;
 portENTER_CRITICAL(&supervisor_lock);
 active=supervisor_active; fault=supervisor_fault; updating=supervisor_updating;
 stage=supervisor_stage; uploaded=supervisor_uploaded;
 elapsed=active?(esp_timer_get_time()-supervisor_started)/1000:0;
 progress=active?(esp_timer_get_time()-supervisor_last_progress)/1000:0;
 portEXIT_CRITICAL(&supervisor_lock);
 snprintf(out,sizeof out,"{\"ok\":true,\"supervisor\":17,\"update_mode\":%s,\"module_abi\":%u,\"text_address\":%lu,\"data_address\":%lu,\"module_ready\":%s,\"fault\":%s,\"busy\":%s,\"updating\":%s,\"uploaded\":%lu,\"stage\":%lu,\"elapsed_ms\":%lld,\"progress_age_ms\":%lld,\"reset_reason\":%d}\n",
  module_runtime_update_allowed() && !supervisor_reboot_required?"true":"false",(unsigned)MODULE_ABI_VERSION,(unsigned long)module_runtime_text_address(),(unsigned long)module_runtime_data_address(),module_allowed_this_boot && !fault && module_runtime_ready()?"true":"false",fault?"true":"false",active||updating||module_runtime_busy()?"true":"false",updating?"true":"false",(unsigned long)uploaded,(unsigned long)stage,(long long)elapsed,(long long)progress,(int)esp_reset_reason());
 reply(fd,out);
}
static bool supervisor_hex(const char *s,uint8_t digest[32]) {
 if(!s || strlen(s)!=64) return false;
 for(unsigned i=0;i<32;i++) {
  unsigned value=0;
  for(unsigned k=0;k<2;k++) { char c=s[2*i+k]; unsigned v;
   if(c>='0'&&c<='9') v=(unsigned)(c-'0');
   else if(c>='a'&&c<='f') v=(unsigned)(c-'a'+10);
   else if(c>='A'&&c<='F') v=(unsigned)(c-'A'+10);
   else return false;
   value=value*16+v;
  } digest[i]=(uint8_t)value;
 } return true;
}
static void supervisor_serve(int fd,bool *owned) {
 char line[REQUEST_MAX+1]; size_t used=0;
 int64_t deadline=esp_timer_get_time()+HEADER_US;
 while(used<REQUEST_MAX) {
  if(!socket_exact(fd,line+used,1,false,deadline)) { failure(fd,"request timeout or disconnect"); return; }
  if(!line[used]) { failure(fd,"NUL in JSON request"); return; }
  if(line[used++]=='\n') break;
 }
 if(!used || line[used-1]!='\n') { failure(fd,"request too long"); return; }
 line[used]=0;
 cJSON *r=cJSON_ParseWithLengthOpts(line,used+1,NULL,true);
 if(!r || !cJSON_IsObject(r)) { cJSON_Delete(r); failure(fd,"invalid JSON"); return; }
 unsigned seen=0; bool valid=strstr(line,"\\u0000")==NULL; cJSON *item;
 cJSON_ArrayForEach(item,r) {
  unsigned b=!strcmp(item->string,"token")?1:!strcmp(item->string,"op")?2:!strcmp(item->string,"size")?4:!strcmp(item->string,"sha256")?8:0;
  if(!b || (seen&b)) valid=false;
  seen|=b;
 }
 cJSON *token=cJSON_GetObjectItemCaseSensitive(r,"token"), *op=cJSON_GetObjectItemCaseSensitive(r,"op");
 if(!valid || !cJSON_IsString(token) || !authenticated(token->valuestring) || !cJSON_IsString(op)) { cJSON_Delete(r); failure(fd,"authentication or request rejected"); return; }
 bool upload=!strcmp(op->valuestring,"module-upload");
 if((upload && seen!=15) || (!upload && seen!=3)) { cJSON_Delete(r); failure(fd,"invalid fields for operation"); return; }
 if(!strcmp(op->valuestring,"status")) { cJSON_Delete(r); supervisor_status(fd); return; }
 if(!strcmp(op->valuestring,"esp-restart")) {
  cJSON_Delete(r); reply(fd,"{\"ok\":true}\n"); shutdown(fd,SHUT_WR); vTaskDelay(pdMS_TO_TICKS(100)); esp_restart(); return;
 }
 if(!strcmp(op->valuestring,"enter-update")) {
  cJSON_Delete(r);
  /* Footer-only invalidation: no UART ownership, busy wait or hot unload.
   * Old body stays intact until whole-chip reset stops tasks/ISRs. */
  if(module_runtime_invalidate()) { failure(fd,"module footer erase/verify failed; retry enter-update"); return; }
  portENTER_CRITICAL(&supervisor_lock);
  supervisor_active=false; supervisor_record_write_locked();
  portEXIT_CRITICAL(&supervisor_lock);
  reply(fd,"{\"ok\":true,\"reboot_required\":true}\n");
  shutdown(fd,SHUT_WR); vTaskDelay(pdMS_TO_TICKS(100)); esp_restart(); return;
 }
 if(upload && (!module_runtime_update_allowed() || supervisor_reboot_required)) {
  cJSON_Delete(r); failure(fd,"run enter-update and wait for ESP reboot before module-upload"); return;
 }
 if(!upload && strcmp(op->valuestring,"clear-fault") && strcmp(op->valuestring,"module-test-hang")) { cJSON_Delete(r); failure(fd,"unknown operation"); return; }
 uint32_t size=0; uint8_t digest[32];
 cJSON *hash=cJSON_GetObjectItemCaseSensitive(r,"sha256");
 const esp_partition_t *partition=esp_partition_find_first(ESP_PARTITION_TYPE_DATA,0x41,"business");
 if(upload && (!partition || !number(cJSON_GetObjectItemCaseSensitive(r,"size"),&size) || size==0 || size>partition->size-4096 || !cJSON_IsString(hash) || !supervisor_hex(hash->valuestring,digest))) { cJSON_Delete(r); failure(fd,"invalid module size or sha256"); return; }
 bool clear=!strcmp(op->valuestring,"clear-fault"); cJSON_Delete(r);
 if(!control_acquire()) { failure(fd,"UART or module busy"); return; } *owned=true;
 if(supervisor_active || module_runtime_busy()) { failure(fd,"module busy"); return; }
 if(clear) {
  if(!module_allowed_this_boot || supervisor_reboot_required) { failure(fd,"update boot; restart ESP to activate committed module"); return; }
  if(supervisor_init_submitted && !module_runtime_ready()) { failure(fd,"initialization already attempted; restart ESP to activate module"); return; }
  portENTER_CRITICAL(&supervisor_lock); supervisor_fault=false; supervisor_record_write_locked(); portEXIT_CRITICAL(&supervisor_lock);
  int rc=module_runtime_ready()?0:supervisor_start();
  if(rc) failure(fd,"module initialization could not be queued"); else reply(fd,"{\"ok\":true,\"initializing\":true}\n"); return;
 }
 if(!upload) {
  if(supervisor_fault || !module_runtime_ready()) { failure(fd,"module unavailable or fault latched"); return; }
  /* Persistent storage: management must remain available during this test. */
  static supervisor_job test_job;
  test_job=(supervisor_job){.kind=SJ_HANG};
  supervisor_job *pending=&test_job;
  portENTER_CRITICAL(&supervisor_lock);
  supervisor_fault=true; supervisor_active=true;
  supervisor_started=supervisor_last_progress=esp_timer_get_time();
  supervisor_limit_us=60000000LL;
  supervisor_record_write_locked();
  portEXIT_CRITICAL(&supervisor_lock);
  if(xQueueSend(supervisor_queue,&pending,0)!=pdTRUE) {
   portENTER_CRITICAL(&supervisor_lock); supervisor_active=false; supervisor_record_write_locked(); portEXIT_CRITICAL(&supervisor_lock);
   failure(fd,"worker queue busy; fault latched");
  } else reply(fd,"{\"ok\":true,\"test\":\"watchdog-reset\"}\n");
  return;
 }
 portENTER_CRITICAL(&supervisor_lock); supervisor_updating=true; supervisor_uploaded=0; portEXIT_CRITICAL(&supervisor_lock);
 /* Runtime MUST invalidate and erase/verify footer BEFORE touching body. */
 int rc=module_runtime_install_begin(size);
 if(rc) { module_runtime_invalidate(); failure(fd,"module install begin failed"); goto upload_done; }
 if(!reply(fd,"{\"ok\":true,\"ready\":true}\n")) goto upload_abort;
 deadline=esp_timer_get_time()+UPLOAD_US;
 uint8_t block[1024];
 for(uint32_t offset=0;offset<size;) {
  size_t n=size-offset; if(n>sizeof block) n=sizeof block;
  if(!socket_exact(fd,block,n,false,deadline)) { failure(fd,"module upload timeout or disconnect"); goto upload_abort; }
  if(module_runtime_install_write(offset,block,n)) { failure(fd,"module write failed"); goto upload_abort; }
  offset+=(uint32_t)n;
  portENTER_CRITICAL(&supervisor_lock); supervisor_uploaded=offset; portEXIT_CRITICAL(&supervisor_lock);
 }
 /* Finish verifies stored bytes/format and commits footer; NEVER calls entry. */
 if(module_runtime_install_finish(digest)) { failure(fd,"module hash, format or commit failed"); goto upload_abort; }
 portENTER_CRITICAL(&supervisor_lock);
 supervisor_fault=false; supervisor_active=false; supervisor_stage=0; supervisor_limit_us=0; supervisor_reboot_required=true;
 supervisor_record_write_locked();
 portEXIT_CRITICAL(&supervisor_lock);
 reply(fd,"{\"ok\":true,\"reboot_required\":true}\n"); goto upload_done;
 upload_abort: module_runtime_invalidate();
 upload_done:
 portENTER_CRITICAL(&supervisor_lock); supervisor_updating=false; portEXIT_CRITICAL(&supervisor_lock);
}
static void supervisor_management(void *arg) {
 (void)arg; int server=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP); if(server<0) {vTaskDelete(NULL);return;}
 int one=1; setsockopt(server,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one);
 struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(SUPERVISOR_PORT),.sin_addr.s_addr=htonl(INADDR_ANY)};
 if(bind(server,(struct sockaddr *)&addr,sizeof addr) || listen(server,2)) {close(server);vTaskDelete(NULL);return;}
 /* Bind/listen FIRST. A stuck module entry cannot block network startup or
  * management accept: INIT is asynchronously owned by static storage. */
 if(module_allowed_this_boot && !supervisor_fault && supervisor_start()) ESP_LOGE(TAG,"module init queue failed");
 for(;;) {
  xEventGroupWaitBits(events,CONNECTED,pdFALSE,pdTRUE,portMAX_DELAY);
  int fd=accept(server,NULL,NULL); if(fd<0) {vTaskDelay(pdMS_TO_TICKS(100));continue;}
  struct timeval timeout={.tv_sec=2}; setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof timeout); setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof timeout);
  bool owned=false; supervisor_serve(fd,&owned); if(owned) control_release(); shutdown(fd,SHUT_RDWR); close(fd);
 }
}
static void supervisor_monitor(void *arg) {
 (void)arg;
 for(;;) {
  bool expired;
  portENTER_CRITICAL(&supervisor_lock);
  expired=rmb_supervisor_deadline_expired(supervisor_active,supervisor_started,esp_timer_get_time(),supervisor_limit_us);
  if(expired) {supervisor_fault=true;supervisor_record_write_locked();}
  portEXIT_CRITICAL(&supervisor_lock);
  if(expired) esp_restart();
  vTaskDelay(pdMS_TO_TICKS(250));
 }
}
static void supervisor_init(void) {
 /* prepare ran even on fault/quarantine boots; ready alone is NOT permission. */
 rmb_supervisor_entry_capture(&supervisor_entry,!module_runtime_update_allowed());
 bool valid=rmb_supervisor_record_valid(&supervisor_rtc);
 esp_reset_reason_t reason=esp_reset_reason();
 supervisor_fault=rmb_supervisor_boot_fault(&supervisor_rtc,reason==ESP_RST_TASK_WDT || reason==ESP_RST_INT_WDT || reason==ESP_RST_WDT || reason==ESP_RST_PANIC);
 supervisor_active=false; supervisor_stage=valid?supervisor_rtc.stage:0; supervisor_record_write_locked();
 esp_task_wdt_config_t config={.timeout_ms=20000,.idle_core_mask=0,.trigger_panic=true};
 esp_err_t rc=esp_task_wdt_reconfigure(&config);
 if(rc==ESP_ERR_INVALID_STATE) rc=esp_task_wdt_init(&config);
 ESP_ERROR_CHECK(rc);
 supervisor_queue=xQueueCreate(1,sizeof(supervisor_job *)); ESP_ERROR_CHECK(supervisor_queue?ESP_OK:ESP_ERR_NO_MEM);
 ESP_ERROR_CHECK(xTaskCreate(supervisor_worker,"module-worker",8192,NULL,3,&supervisor_worker_handle)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
 ESP_ERROR_CHECK(xTaskCreate(supervisor_monitor,"module-health",3072,NULL,4,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
 /* app_main has already prepared nonexecuting XIP before NVS/Wi-Fi. */
 /* Management starts INIT only AFTER its socket is listening. */
}
