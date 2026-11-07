/* ABI3 minimal service; imports are exact-base globals, not a stable host API. */
#include "module_abi.h"
#include "cJSON.h"
#include <stdio.h>
#include <string.h>
#include <limits.h>
#ifndef SERVICE_VARIANT
#error SERVICE_VARIANT must be selected by manifest
#endif
#if SERVICE_VARIANT != 1 && SERVICE_VARIANT != 2
#error unsupported SERVICE_VARIANT
#endif
extern bool authenticated(const char *);
extern bool socket_exact(int,void *,size_t,bool,int64_t);
extern bool reply(int,const char *);
extern void failure(int,const char *);
extern int64_t esp_timer_get_time(void);
static unsigned requests, echoes;
static bool owned;
static int64_t started;
static int no_validate(const rom_device *d,const rom_source *s,uint32_t n,uint32_t c,int r) {(void)d;(void)s;(void)n;(void)c;(void)r;return ROM_E_CAP;}
static int no_download(const rom_io *i,const rom_device *d,const rom_source *s,uint32_t n,uint32_t c,int r,rom_trace_fn f,void *x) {(void)i;(void)d;(void)s;(void)n;(void)c;(void)r;(void)f;(void)x;return ROM_E_CAP;}
static int no_probe(const rom_io *i,const rom_device *d,rom_probe_result *r,rom_trace_fn f,void *x) {(void)i;(void)d;(void)r;(void)f;(void)x;return ROM_E_CAP;}
static int no_restart(const rom_io *i) {(void)i;return ROM_E_CAP;}
/* cJSON stores strings as C strings: reject raw and escaped NUL before parsing.
 * Skip escaped backslashes so literal text "\\u0000" remains representable. */
static bool no_nul_escape(const char *s,size_t n) {
 for(size_t i=0;i<n;i++) if(s[i]=='\\' && ++i<n) {
  if(s[i]=='u' && i+4<n && !memcmp(s+i+1,"0000",4)) return false;
 }
 return true;
}
static void serve(int fd) {
 char line[1025]; size_t n=0; int64_t deadline=esp_timer_get_time()+10000000LL;
 for(;;) {
  char ch;
  if(!socket_exact(fd,&ch,1,false,deadline)) {failure(fd,"request timeout or closed");return;}
  if(ch=='\n') break;
  if(!ch || n==1024) {failure(fd,"invalid request length or NUL");return;}
  line[n++]=ch;
 }
 line[n]=0;
 if(!no_nul_escape(line,n)) {failure(fd,"NUL unsupported");return;}
 const char *end=0;
 cJSON *root=cJSON_ParseWithLengthOpts(line,n+1,&end,true);
 if(!root || !end || end!=line+n || !cJSON_IsObject(root)) {cJSON_Delete(root);failure(fd,"invalid JSON");return;}
 cJSON *token=0,*op=0,*text=0; bool valid=true;
 for(cJSON *p=root->child;p;p=p->next) {
  if(!p->string || !cJSON_IsString(p)) {valid=false;break;}
  cJSON **slot=0;
  if(!strcmp(p->string,"token")) slot=&token;
  else if(!strcmp(p->string,"op")) slot=&op;
  else if(!strcmp(p->string,"text")) slot=&text;
  else {valid=false;break;}
  if(*slot) {valid=false;break;} *slot=p;
 }
 if(!valid || !token || !op || strlen(token->valuestring)>128) {cJSON_Delete(root);failure(fd,"invalid schema");return;}
 if(!authenticated(token->valuestring)) {cJSON_Delete(root);failure(fd,"authentication failed");return;}
 bool echo=!strcmp(op->valuestring,"echo"), info=!strcmp(op->valuestring,"service-info");
 if((!echo&&!info) || (echo&&(!text||strlen(text->valuestring)>128)) || (info&&text)) {cJSON_Delete(root);failure(fd,"invalid operation or text");return;}
 char copy[129]={0};
 if(echo) memcpy(copy,text->valuestring,strlen(text->valuestring)+1);
 /* Never retain pointers into parse tree or borrowed fd storage. */
 cJSON_Delete(root);
 if(echo && SERVICE_VARIANT==2) for(size_t i=0;copy[i];i++) if(copy[i]>='a'&&copy[i]<='z') copy[i]=(char)(copy[i]-'a'+'A');
 cJSON *value=cJSON_CreateString(copy); char *quoted=value?cJSON_PrintUnformatted(value):0;
 if(!quoted) {cJSON_Delete(value);failure(fd,"allocation failed");return;}
 if(requests<UINT_MAX) requests++;
 if(echo&&echoes<UINT_MAX) echoes++;
 char output[1152];
 int used=snprintf(output,sizeof output,"{\"ok\":true,\"service\":\"hello\",\"version\":\"%s\",\"op\":\"%s\",\"requests\":%u,\"echoes\":%u,\"uptime_ms\":%lld,\"state_reset\":\"on module entry\",\"rom_supported\":false,\"text\":%s}\n",SERVICE_VARIANT==1?"A":"B",echo?"echo":"service-info",requests,echoes,(long long)((esp_timer_get_time()-started)/1000),quoted);
 cJSON_free(quoted); cJSON_Delete(value);
 if(used<0 || (size_t)used>=sizeof output) {failure(fd,"response overflow");return;}
 (void)reply(fd,output);
}
static int dispatch(uint32_t channel,void *argument) {
 if(channel==3) {if(owned)return ROM_E_CAP;owned=true;return 0;}
 if(channel==4) {owned=false;return 0;}
 if(channel!=1 || !argument) return ROM_E_CAP;
 serve(*(const int *)argument);return 0;
}
static const module_ops ops={MODULE_ABI_VERSION,sizeof(module_ops),no_validate,no_download,no_probe,no_restart,dispatch};
const module_ops *module_entry(const module_host *host) {
 if(!host || host->abi!=MODULE_ABI_VERSION || host->size!=sizeof *host) return 0;
 requests=echoes=0;owned=false;started=esp_timer_get_time();return &ops;
}
const unsigned char module_base_binding[32] __attribute__((section(".module_base_id"),used))={1};
