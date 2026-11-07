#!/usr/bin/env python3
"""Execute actual backend SPI lifecycle with SDK-call shims, ASan/UBSan.
Not a driver or physical wire simulation; checks backend resource lifetimes.
"""
from pathlib import Path
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'module/platform/esp_idf_io.c').read_text()
spi = source[source.index('static spi_device_handle_t'):source.index('esp_err_t esp_io_uart_mode(')]
stubs = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef int esp_err_t;
typedef void *spi_device_handle_t;
typedef struct {int mosi_io_num,miso_io_num,sclk_io_num,quadwp_io_num,quadhd_io_num,max_transfer_sz;} spi_bus_config_t;
typedef struct {int clock_speed_hz,mode,spics_io_num,queue_size;} spi_device_interface_config_t;
typedef struct {unsigned length,rxlength;const void *tx_buffer;void *rx_buffer;} spi_transaction_t;
#define BOARD_SPI_MOSI 1
#define BOARD_SPI_MISO 0
#define BOARD_SPI_SCLK 7
#define BOARD_SPI_CS 10
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_INVALID_ARG 3
#define SPI2_HOST 2
#define SPI_DMA_DISABLED 0
#define pdMS_TO_TICKS(x) (x)
static int init_rc,add_rc,queue_rc,result_rc,remove_rc,free_rc;
static unsigned init_calls,add_calls,queue_calls,result_calls,remove_calls,free_calls;
static bool wrong_result;
static spi_transaction_t *queued;
static int spi_bus_initialize(int host,const spi_bus_config_t *b,int dma) {
 assert(host==2&&!dma&&b->mosi_io_num==1&&b->miso_io_num==0&&b->sclk_io_num==7&&b->max_transfer_sz==64);init_calls++;return init_rc;
}
static int spi_bus_add_device(int host,const spi_device_interface_config_t *d,spi_device_handle_t *h) {
 assert(host==2&&d->spics_io_num==10&&d->mode==0&&d->queue_size==1);add_calls++;if(!add_rc)*h=(void*)1;return add_rc;
}
static int spi_device_queue_trans(spi_device_handle_t h,spi_transaction_t *t,int ticks) {
 assert(h==(void*)1&&ticks==100&&t->length==512&&t->rxlength==512);queue_calls++;if(!queue_rc)queued=t;return queue_rc;
}
static int spi_device_get_trans_result(spi_device_handle_t h,spi_transaction_t **t,int ticks) {
 assert(h==(void*)1&&(ticks==100||ticks==0));result_calls++;if(result_rc)return result_rc;
 *t=wrong_result?NULL:queued;if(!wrong_result)memset(queued->rx_buffer,0x5a,64);return 0;
}
static int spi_bus_remove_device(spi_device_handle_t h){assert(h==(void*)1);remove_calls++;return remove_rc;}
static int spi_bus_free(int host){assert(host==2);free_calls++;return free_rc;}
'''
tests = r'''
static void reset(void) {
 assert(!esp_io_spi_busy());init_rc=add_rc=queue_rc=result_rc=remove_rc=free_rc=0;
 init_calls=add_calls=queue_calls=result_calls=remove_calls=free_calls=0;wrong_result=false;queued=NULL;
}
int main(void) {
 const char *error;uint8_t tx[64]={1},rx[64]={0};
 reset();assert(!esp_io_spi_open(100000,&error));assert(!esp_io_spi_transfer(tx,rx,&error));assert(rx[0]==0x5a&&rx[63]==0x5a);assert(queued->tx_buffer!=tx&&queued->rx_buffer!=rx);assert(!esp_io_spi_close()&&!esp_io_spi_busy());
 reset();init_rc=10;assert(esp_io_spi_open(100000,&error)==10);assert(!esp_io_spi_close()&&!free_calls);
 reset();add_rc=11;assert(esp_io_spi_open(100000,&error)==11&&esp_io_spi_busy());assert(!esp_io_spi_close()&&free_calls==1&&!remove_calls);
 reset();assert(!esp_io_spi_open(100000,&error));queue_rc=12;assert(esp_io_spi_transfer(tx,rx,&error)==12);assert(!esp_io_spi_close()&&!esp_io_spi_busy());
 reset();assert(!esp_io_spi_open(100000,&error));result_rc=13;assert(esp_io_spi_transfer(tx,rx,&error)==13);assert(spi_pending);uint8_t saved[64];memcpy(saved,spi_tx,64);tx[0]=42;
 assert(esp_io_spi_transfer(tx,rx,&error)==ESP_ERR_INVALID_STATE);assert(!memcmp(saved,spi_tx,64)&&queue_calls==1);
 assert(esp_io_spi_close()==13&&esp_io_spi_busy()&&!remove_calls&&!free_calls);assert(esp_io_spi_open(100000,&error)==ESP_ERR_INVALID_STATE&&init_calls==1);
 result_rc=0;assert(!esp_io_spi_close()&&!esp_io_spi_busy()&&remove_calls==1&&free_calls==1);
 reset();assert(!esp_io_spi_open(100000,&error));wrong_result=true;assert(esp_io_spi_transfer(tx,rx,&error)==ESP_FAIL);assert(esp_io_spi_close()==ESP_FAIL&&!remove_calls);wrong_result=false;assert(!esp_io_spi_close());
 reset();assert(!esp_io_spi_open(100000,&error));remove_rc=14;assert(esp_io_spi_close()==14&&esp_io_spi_busy()&&!free_calls);remove_rc=0;free_rc=15;assert(esp_io_spi_close()==15&&esp_io_spi_busy());free_rc=0;assert(!esp_io_spi_close()&&!esp_io_spi_busy());
 puts("PASS actual SPI backend: static buffers, completion/reap, queue/open/remove/free failures, retained in-flight storage and no reinitialization/overwrite/free while pending");
}
'''
with tempfile.TemporaryDirectory(prefix='spi-backend-native-') as work:
    cfile = Path(work) / 'backend.c'
    exe = Path(work) / 'backend'
    cfile.write_text(stubs + spi + tests)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g',str(cfile),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
