"""Strict production ABI4 management transactions over native sockets.

Uses the real supervisor, runtime, update core, cJSON and SHA-256; only native
platform/entry seams are supplied by test_supervisor_startup. No UART ownership
or obsolete ROM request scaffolding is part of the generic resident ABI.
"""

from test_supervisor_startup import build_run

POST = r"""
static char result[4096],upload[256];
static uint8_t body[76],digest[32];
static void run(const char *text,const void *payload,size_t n) {
 int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));
 assert(write(f[0],text,strlen(text))==(ssize_t)strlen(text));
 if(n)assert(write(f[0],payload,n)==(ssize_t)n);
 shutdown(f[0],SHUT_WR);supervisor_serve(f[1]);shutdown(f[1],SHUT_WR);
 size_t used=0;ssize_t got;
 while((got=read(f[0],result+used,sizeof result-1-used))>0){used+=(size_t)got;assert(used<sizeof result-1);}
 assert(got==0);result[used]=0;close(f[0]);close(f[1]);
}
#define TOKEN "\"token\":\"native-supervisor-test-token\""
#define REQUEST(op) "{" TOKEN ",\"op\":\"" op "\"}\n"
#define HASH "1212121212121212121212121212121212121212121212121212121212121212"
static void update_boot(void) {reset_boot(false);image(body);assert(!mbedtls_sha256(body,sizeof body,digest,0));char hash[65];for(unsigned i=0;i<32;i++)snprintf(hash+2*i,3,"%02x",digest[i]);snprintf(upload,sizeof upload,"{" TOKEN ",\"op\":\"module-upload\",\"size\":76,\"sha256\":\"%s\"}\n",hash);}
static void failed_upload(void) {
 assert(strstr(result,"\"ok\":false")&&!supervisor_updating&&!entered&&!module_runtime_ready());
 assert(bridge_footer_verify(&update,255)==0&&!supervisor_reboot_required);
}
int main(void) {
 (void)thread;(void)supervisor_progress;assert(supervisor_run(NULL,NULL,1)==-1);
 update_boot();
 const char *bad[]={
 "{\"token\":\"wrong\",\"op\":\"module-upload\",\"size\":76,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"status\",\"op\":\"status\"}\n",
 "{" TOKEN ",\"op\":\"status\",\"extra\":0}\n",
 "{" TOKEN ",\"op\":\"status\",\"size\":8}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":8.5,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":0,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":1044481,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":76,\"sha256\":\"g212121212121212121212121212121212121212121212121212121212121212\"}\n",
 "{" TOKEN ",\"op\":\"sta\\u0000tus\"}\n",
 "{" TOKEN ",\"op\":\"status\"}{}\n",
 "{" TOKEN ",\"op\":\"ack-fault\"}\n",
 "{" TOKEN ",\"op\":\"status\",\"seq\":7}\n"
 };
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){run(bad[i],NULL,0);assert(strstr(result,"\"ok\":false")&&!body_erases&&!body_writes&&!entered);}
 run(REQUEST("status"),NULL,0);assert(strstr(result,"\"ok\":true")&&strstr(result,"\"module_abi\":4")&&strstr(result,"\"update_mode\":true"));
 char long_header[1026];memset(long_header,'a',1024);long_header[1024]='\n';long_header[1025]=0;run(long_header,NULL,0);assert(strstr(result,"request too long")&&!body_erases);
 /* Permission is the immutable boot latch, not ready/fault state. */
 reset_boot(true);image(body);run(upload,body,sizeof body);assert(strstr(result,"enter-update")&&!body_erases);
 supervisor_fault=true;run(upload,body,sizeof body);assert(strstr(result,"enter-update")&&!body_erases);
 update_boot();run(REQUEST("clear-fault"),NULL,0);assert(strstr(result,"restart ESP")&&!supervisor_init_submitted);
 run(upload,body,sizeof body);
 assert(strstr(result,"\"ready\":true")&&strstr(result,"\"reboot_required\":true"));
 assert(supervisor_uploaded==sizeof body&&!supervisor_updating&&supervisor_reboot_required&&!entered&&!restarted&&!module_runtime_ready());
 assert(!bridge_footer_verify(&update,0)&&!memcmp(flash,body,sizeof body));
 unsigned old_writes=body_writes,old_erases=body_erases;
 run(upload,body,sizeof body);assert(strstr(result,"enter-update")&&body_writes==old_writes&&body_erases==old_erases);
 run(REQUEST("clear-fault"),NULL,0);assert(strstr(result,"restart ESP")&&!supervisor_init_submitted);
 /* Independent simulated boots exercise all abort paths with actual storage. */
 update_boot();busy=true;run(upload,body,sizeof body);assert(strstr(result,"busy")&&!supervisor_updating);busy=false;
 supervisor_active=true;run(upload,body,sizeof body);assert(strstr(result,"busy"));supervisor_active=false;
 run(upload,body,3);failed_upload();assert(strstr(result,"disconnect"));
 update_boot();write_error=1;run(upload,body,sizeof body);failed_upload();write_error=0;
 /* Force a real write failure during transfer, not merely padded finish. */
 update_boot();uint8_t large[332]={0};module_header lh;
 memcpy(&lh,body,sizeof lh);lh.image_size=sizeof large;lh.text_size=260;
 memcpy(large,&lh,sizeof lh);uint8_t ld[32];assert(!mbedtls_sha256(large,sizeof large,ld,0));
 char lh_hash[65],lh_request[256];for(unsigned i=0;i<32;i++)snprintf(lh_hash+2*i,3,"%02x",ld[i]);
 snprintf(lh_request,sizeof lh_request,"{" TOKEN ",\"op\":\"module-upload\",\"size\":332,\"sha256\":\"%s\"}\n",lh_hash);
 write_error=1;run(lh_request,large,sizeof large);failed_upload();assert(strstr(result,"module write failed"));write_error=0;
 update_boot();body[75]^=1;run(upload,body,sizeof body);failed_upload();assert(strstr(result,"hash, format or commit"));
 update_boot();body[0]^=1;run(upload,body,sizeof body);failed_upload();
 update_boot();body[44]=1;run(upload,body,sizeof body);failed_upload(); /* base binding */
 update_boot();erase_error=1;run(upload,body,sizeof body);assert(strstr(result,"install begin failed")&&!supervisor_updating&&!entered);erase_error=0;
 update_boot();supervisor_fault=true;supervisor_stage=42;supervisor_limit_us=99;
 run(upload,body,sizeof body);assert(!supervisor_fault&&!supervisor_active&&!supervisor_stage&&!supervisor_limit_us&&supervisor_reboot_required&&!entered&&!restarted);
 const char *danger[]={"esp-restart","clear-fault","module-test-hang","enter-update","ack-fault"};
 for(unsigned i=0;i<sizeof danger/sizeof danger[0];i++){char h[160];snprintf(h,sizeof h,"{\"token\":\"wrong\",\"op\":\"%s\"}\n",danger[i]);run(h,NULL,0);assert(strstr(result,"\"ok\":false")&&!restarted&&!supervisor_active);}
 /* Sequence-specific fault acknowledgement is validated by the real parser. */
 pending_fault=true;run(REQUEST("get-fault"),NULL,0);assert(strstr(result,"\"pending\":true"));
 run("{" TOKEN ",\"op\":\"ack-fault\",\"seq\":6}\n",NULL,0);assert(strstr(result,"\"ok\":false")&&pending_fault);
 run("{" TOKEN ",\"op\":\"ack-fault\",\"seq\":7.5}\n",NULL,0);assert(strstr(result,"\"ok\":false")&&pending_fault);
 run("{" TOKEN ",\"op\":\"ack-fault\",\"seq\":7}\n",NULL,0);assert(strstr(result,"\"ok\":true")&&!pending_fault);
 /* Committed recovery boot: queue static INIT once, no synchronous entry. */
 reset_boot(true);pending_fault=true;supervisor_fault=true;
 run(REQUEST("clear-fault"),NULL,0);assert(strstr(result,"ack-fault required")&&!queued);
 pending_fault=false;run(REQUEST("clear-fault"),NULL,0);assert(strstr(result,"\"initializing\":true")&&queued&&supervisor_active&&!supervisor_fault&&supervisor_init_submitted);
 run(REQUEST("status"),NULL,0);assert(strstr(result,"\"busy\":true"));
 run(REQUEST("clear-fault"),NULL,0);assert(strstr(result,"busy"));
 supervisor_active=false;queued=NULL;run(REQUEST("clear-fault"),NULL,0);assert(strstr(result,"already attempted"));
 run(REQUEST("esp-restart"),NULL,0);assert(restarted==1);
 /* Footer-only failure/retry must bypass a busy or hung generic worker. */
 old_writes=body_writes;old_erases=body_erases;busy=true;supervisor_active=true;erase_error=1;
 run(REQUEST("enter-update"),NULL,0);assert(restarted==1&&strstr(result,"footer erase/verify failed"));erase_error=0;
 run(REQUEST("enter-update"),NULL,0);assert(restarted==2&&invalidated_at<restarted_at&&strstr(result,"\"reboot_required\":true"));
 assert(body_writes==old_writes&&body_erases==old_erases&&!module_runtime_update_allowed());
 busy=false;run(upload,body,sizeof body);assert(strstr(result,"enter-update")&&body_writes==old_writes);
 puts("PASS production ABI4 management + runtime: strict auth/fields/seq, immutable boot latch, real SHA/binding/footer commit, no hot activation, binary upload abort cleanup, busy-worker footer retry before restart");
}
"""

if __name__ == "__main__":
    build_run(POST)
