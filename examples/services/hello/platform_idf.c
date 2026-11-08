#include "platform.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static hello_heartbeat_fn root_heartbeat;
static TaskHandle_t loop_task;
static void idle_event_loop(void *context) {
 (void)context;
 for (;;) {
  root_heartbeat();
  vTaskDelay(pdMS_TO_TICKS(250) ? pdMS_TO_TICKS(250) : 1);
 }
}
int hello_platform_start(hello_heartbeat_fn heartbeat) {
 if(loop_task) return root_heartbeat==heartbeat ? 0 : -1;
 root_heartbeat=heartbeat;
 if(xTaskCreate(idle_event_loop,"hello-idle",2048,0,tskIDLE_PRIORITY+1,&loop_task)!=pdPASS) {
  loop_task=0;return -1;
 }
 return 0;
}
void hello_platform_yield(void) {vTaskDelay(1);}
