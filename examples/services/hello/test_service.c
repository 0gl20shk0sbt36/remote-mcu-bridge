/* UNIT ONLY: actual service C + real SDK cJSON, borrowed base contracts stubbed. */
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "module_abi.h"
#include "cJSON.h"
extern const module_ops *module_entry(const module_host *);
static const unsigned char *input; static size_t remaining; static char output[4096];
static int64_t deadline_seen; static int fd_seen; static int64_t now=1000;
int64_t esp_timer_get_time(void) {return now++;}
bool authenticated(const char *s) {return s&&!strcmp(s,"unit-only-token-1234");}
bool reply(int fd,const char *s) {assert(fd==77);assert(strlen(output)+strlen(s)<sizeof output);strcat(output,s);return true;}
void failure(int fd,const char *s) {(void)s;reply(fd,"{\"ok\":false}\n");}
bool socket_exact(int fd,void *p,size_t n,bool writing,int64_t deadline) {
 assert(fd==77&&!writing&&n==1);fd_seen=fd;
 if(!deadline_seen) deadline_seen=deadline;assert(deadline==deadline_seen);
 assert(deadline>now&&deadline-now<=10000000LL);
 if(!remaining)return false;
 memcpy(p,input,n);input+=n;remaining-=n;return true;
}
static const module_ops *ops;
static cJSON *run_bytes(const void *s,size_t n,bool ok) {
 input=s;remaining=n;output[0]=0;deadline_seen=0;fd_seen=0;int borrowed_fd=77;
 assert(!ops->dispatch(1,&borrowed_fd));assert(borrowed_fd==77&&fd_seen==77);
 cJSON *o=cJSON_Parse(output);assert(o);assert(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(o,"ok"))==ok);return o;
}
static cJSON *run(const char *s,bool ok) {return run_bytes(s,strlen(s),ok);}
static void bad(const char *s) {cJSON *o=run(s,false);assert(!strstr(output,"secret-text"));cJSON_Delete(o);}
int main(void) {
 module_host h={.abi=MODULE_ABI_VERSION,.size=sizeof h};assert(!module_entry(NULL));ops=module_entry(&h);assert(ops);
 assert(ops->validate(0,0,0,0,0)==ROM_E_CAP);assert(ops->download(0,0,0,0,0,0,0,0)==ROM_E_CAP);
 assert(ops->probe(0,0,0,0,0)==ROM_E_CAP);assert(ops->restart(0)==ROM_E_CAP);
 assert(ops->dispatch(2,0)==ROM_E_CAP);assert(ops->dispatch(1,0)==ROM_E_CAP);
 assert(!ops->dispatch(3,0));assert(ops->dispatch(3,0)==ROM_E_CAP);assert(!ops->dispatch(4,0));
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
 ops=module_entry(&h);o=run("{\"token\":\"unit-only-token-1234\",\"op\":\"service-info\"}\n",true);
 assert(cJSON_GetObjectItemCaseSensitive(o,"requests")->valueint==1);assert(cJSON_GetObjectItemCaseSensitive(o,"echoes")->valueint==0);cJSON_Delete(o);
 printf("variant %d actual-C unit tests passed (UNIT ONLY)\n",SERVICE_VARIANT);return 0;
}
