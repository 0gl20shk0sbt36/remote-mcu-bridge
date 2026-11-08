/* Private service helpers; fd-named variables hold opaque borrowed handles. */
/* Never disappear inside a 180s base exact call. Each bounded retry belongs
 * to the calling operation, has an absolute deadline, and yields the scheduler. */
static bool socket_exact(int fd,void *p,size_t n,bool writing,int64_t deadline) {
 uint8_t *bytes=p;
 while(n) {
  if(host->now_us()>=deadline)return false;
  service_io_progress();
  int k=writing?host->net->write(fd,bytes,n,true):host->net->read(fd,bytes,n,true);
  if(k>0) {if((size_t)k>n)return false;bytes+=k;n-=(size_t)k;}
  else if(k!=RMB_IO_AGAIN)return false;
  if(n)vTaskDelay(1);
 }
 return host->now_us()<deadline;
}
static bool authenticated(const char *candidate) {return host->net->authenticated(candidate);}
static bool reply(int fd,const char *text) {return socket_exact(fd,(void *)text,strlen(text),true,host->now_us()+1000000LL);}
static void failure(int fd,const char *message) {
 cJSON *o=cJSON_CreateObject();if(!o)return;
 cJSON_AddBoolToObject(o,"ok",false);cJSON_AddStringToObject(o,"error",message);
 char *s=cJSON_PrintUnformatted(o);if(s){reply(fd,s);reply(fd,"\n");cJSON_free(s);}cJSON_Delete(o);
}
static bool number(cJSON *item,uint32_t *value) {
 if(!cJSON_IsNumber(item)||!isfinite(item->valuedouble)||item->valuedouble<0||item->valuedouble>UINT32_MAX||floor(item->valuedouble)!=item->valuedouble)return false;
 *value=(uint32_t)item->valuedouble;return true;
}
static bool stream_baud_allowed(unsigned baud) {
 return baud==9600||baud==19200||baud==38400||baud==57600||baud==115200;
}
