/* UNIT ONLY: actual service C + real SDK cJSON, borrowed base contracts stubbed. */
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "rmb/service.h"
#include "cJSON.h"
extern int module_entry(const rmb_host *);
static rmb_connection_fn handler;static void *handler_context;
static unsigned listeners;static int listen_result;
static int fake_listen(uint16_t port,uint32_t flags,rmb_connection_fn callback,void *context) {assert(port==3333&&flags==RMB_NET_AUTH_REPLAY&&callback);listeners++;handler=callback;handler_context=context;return listen_result;}
static const unsigned char *input; static size_t remaining; static char output[4096];
static int64_t deadline_seen; static int fd_seen; static int64_t now=1000;
#include "platform.h"
static hello_heartbeat_fn heartbeat;
static int start_fail,open_fail,active=-1,next_id=0,root_id=-1;
static unsigned root_opens,callback_opens,closes,root_feeds,active_feeds,yields;
static unsigned read_again,write_again;
static bool stall,write_stall,write_error,feed_error;
int hello_platform_start(hello_heartbeat_fn hook) {if(start_fail)return -1;heartbeat=hook;return 0;}
static int wd_open(const char *role,uint32_t timeout) {
 assert(timeout>=1000&&timeout<=60000);
 if(open_fail)return -1;
 int id=next_id++;
 if(!strcmp(role,"hello.root")) {assert(root_id<0);root_id=id;root_opens++;}
 else {assert(!strcmp(role,"hello.callback")&&active<0);active=id;callback_opens++;}
 return id;
}
static int wd_feed(int id,uint32_t checkpoint) {
 assert(checkpoint);
 if(id==root_id)root_feeds++;
 else {assert(id==active);active_feeds++;if(feed_error)return -1;}
 return 0;
}
static int wd_close(int id) {assert(id==active&&id!=root_id);active=-1;closes++;return 0;}
void hello_platform_yield(void) {
 yields++;now+=1000;
 unsigned before=active_feeds;assert(heartbeat);heartbeat();
 assert(active_feeds==before); /* Root heartbeat must never rescue active work. */
}
int64_t fake_now_us(void) {return now++;}
static bool fake_authenticated(const char *s) {return s&&!strcmp(s,"unit-only-token-1234");}
static int fake_read(int fd,void *p,size_t n,bool nonblocking) {
 assert(nonblocking&&fd==77&&n==1&&active>=0);fd_seen=fd;
 if(stall)return RMB_IO_AGAIN;
 if(read_again){read_again--;return RMB_IO_AGAIN;}
 if(!remaining)return 0;
 memcpy(p,input,n);input+=n;remaining-=n;return (int)n;
}
static int fake_write(int fd,const void *p,size_t n,bool nonblocking) {
 assert(nonblocking&&fd==77&&n<=128&&active>=0);
 if(write_error)return RMB_IO_ERROR;
 if(write_stall)return RMB_IO_AGAIN;
 if(write_again){write_again--;return RMB_IO_AGAIN;}
 if(n>3)n=3; /* Exercise partial nonblocking writes. */
 size_t used=strlen(output);assert(used+n<sizeof output);
 memcpy(output+used,p,n);output[used+n]=0;return (int)n;
}

static cJSON *run_bytes(const void *s,size_t n,bool ok) {
 input=s;remaining=n;output[0]=0;deadline_seen=0;fd_seen=0;int borrowed_fd=77;
 unsigned before=closes;handler(borrowed_fd,handler_context);assert(borrowed_fd==77&&fd_seen==77);assert(closes==before+1&&active<0);
 cJSON *o=cJSON_Parse(output);assert(o);assert(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(o,"ok"))==ok);return o;
}
static cJSON *run(const char *s,bool ok) {return run_bytes(s,strlen(s),ok);}
static void bad(const char *s) {cJSON *o=run(s,false);assert(!strstr(output,"secret-text"));cJSON_Delete(o);}
int main(void) {
 rmb_net_api net={.abi=RMB_SERVICE_ABI,.size=sizeof net,.listen=fake_listen,.read=fake_read,.write=fake_write,.authenticated=fake_authenticated};
 rmb_host h={.abi=RMB_SERVICE_ABI,.size=sizeof h,.net=&net,.now_us=fake_now_us,.watchdog_open=wd_open,.watchdog_feed=wd_feed,.watchdog_close=wd_close};
 assert(module_entry(NULL)<0);rmb_host invalid=h;invalid.abi=3;assert(module_entry(&invalid)<0);
 invalid=h;invalid.net=NULL;assert(module_entry(&invalid)<0);
 invalid=h;invalid.size=sizeof h-1;assert(module_entry(&invalid)<0);
 net.abi=3;assert(module_entry(&h)<0);net.abi=RMB_SERVICE_ABI;
 net.size=sizeof net-1;assert(module_entry(&h)<0);net.size=sizeof net;
 invalid=h;invalid.now_us=NULL;assert(module_entry(&invalid)<0);
 invalid=h;invalid.watchdog_open=NULL;assert(module_entry(&invalid)<0);
 invalid=h;invalid.watchdog_feed=NULL;assert(module_entry(&invalid)<0);
 invalid=h;invalid.watchdog_close=NULL;assert(module_entry(&invalid)<0);
 net.read=NULL;assert(module_entry(&h)<0);net.read=fake_read;
 net.write=NULL;assert(module_entry(&h)<0);net.write=fake_write;
 net.listen=NULL;assert(module_entry(&h)<0);net.listen=fake_listen;
 net.authenticated=NULL;assert(module_entry(&h)<0);net.authenticated=fake_authenticated;
 open_fail=1;assert(module_entry(&h)<0);assert(root_opens==0);open_fail=0;
 start_fail=1;assert(module_entry(&h)<0);assert(root_opens==1&&closes==0);start_fail=0;
 listen_result=-1;assert(module_entry(&h)==-1);listen_result=0;listeners=0;
 assert(root_opens==1&&closes==0);
 read_again=4;write_again=3;
 assert(!module_entry(&h));assert(listeners==1&&handler);
 cJSON *o=run("{\"token\":\"unit-only-token-1234\",\"op\":\"echo\",\"text\":\"a\\\"\\\\\\t中\"}\n",true);
 const char *want=SERVICE_VARIANT==1?"a\"\\\t中":"A\"\\\t中";
 assert(!strcmp(cJSON_GetObjectItemCaseSensitive(o,"text")->valuestring,want));cJSON_Delete(o);
 bad("{\"token\":\"wrong\",\"op\":\"echo\",\"text\":\"secret-text\"}\n");
 bad("{\"token\":\"unit-only-token-1234\",\"op\":\"echo\",\"op\":\"echo\",\"text\":\"a\"}\n");
 bad("{\"token\":\"unit-only-token-1234\",\"op\":\"service-info\",\"unknown\":\"x\"}\n");
 bad("{\"token\":\"unit-only-token-1234\",\"op\":\"echo\",\"text\":\"\\u0000suffix\"}\n");
 bad("{\"token\":\"unit-only-token-1234\",\"op\":\"service-info\"} {}\n");
 bad("{\"token\":\"unit-only-token-1234\",\"op\":\"echo\",\"text\":42}\n");
 bad("{\"token\":\"unit-only-token-1234\",\"op\":\"service-info\",\"text\":\"x\"}\n");
 bad("{\"token\":\"unit-only-token-1234\",\"op\":\"echo\"}\n");
 bad("[]\n");bad("\n");bad("{}");
 const char nul[]={'{',0,'}','\n'};o=run_bytes(nul,sizeof nul,false);cJSON_Delete(o);
 char huge[1100];memset(huge,'x',sizeof huge);huge[1098]='\n';huge[1099]=0;bad(huge);
 char request[400],text[130];memset(text,'a',128);text[128]=0;
 snprintf(request,sizeof request,"{\"token\":\"unit-only-token-1234\",\"op\":\"echo\",\"text\":\"%s\"}\n",text);o=run(request,true);cJSON_Delete(o);
 text[128]='a';text[129]=0;snprintf(request,sizeof request,"{\"token\":\"unit-only-token-1234\",\"op\":\"echo\",\"text\":\"%s\"}\n",text);bad(request);
 /* Poison caller-owned request after return; next operation must not use it. */
 memset(request,0xa5,sizeof request);
 o=run("{\"token\":\"unit-only-token-1234\",\"op\":\"service-info\"}\n",true);
 assert(cJSON_GetObjectItemCaseSensitive(o,"requests")->valueint==3);assert(cJSON_GetObjectItemCaseSensitive(o,"echoes")->valueint==2);cJSON_Delete(o);
 assert(!module_entry(&h));o=run("{\"token\":\"unit-only-token-1234\",\"op\":\"service-info\"}\n",true);
 assert(cJSON_GetObjectItemCaseSensitive(o,"requests")->valueint==1);assert(cJSON_GetObjectItemCaseSensitive(o,"echoes")->valueint==0);cJSON_Delete(o);
 /* Persistent root and bounded retry/read deadline, including callback cleanup. */
 assert(root_opens==1&&root_feeds>0&&yields>0&&callback_opens==closes);
 unsigned before=closes;open_fail=1;handler(77,handler_context);open_fail=0;assert(closes==before);
 stall=true;int64_t start=now;o=run("unused",false);cJSON_Delete(o);stall=false;
 assert(now-start>=10000000LL&&now-start<11000000LL);
 input=(const unsigned char *)"{}\n";remaining=3;output[0]=0;write_error=true;
 handler(77,handler_context);write_error=false;assert(active<0&&callback_opens==closes);
 input=(const unsigned char *)"{}\n";remaining=3;output[0]=0;write_stall=true;start=now;
 handler(77,handler_context);write_stall=false;assert(active<0&&callback_opens==closes);
 assert(now-start>=1000000LL&&now-start<2100000LL);
 feed_error=true;handler(77,handler_context);feed_error=false;assert(active<0&&callback_opens==closes);
 unsigned af=active_feeds,rf=root_feeds;hello_root_heartbeat();assert(active_feeds==af&&root_feeds==rf+1);
 printf("variant %d actual-C ABI4 watchdog tests passed (UNIT ONLY)\n",SERVICE_VARIANT);return 0;
}
