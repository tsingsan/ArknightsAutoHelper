import itertools
import sys
import time
import signal

from Arknights.shell_next import _create_helper

tasks = [("1-7", 99)]

if __name__ == '__main__':

    helper = _create_helper()
    helper.use_refill = True
    helper.refill_with_item = True
    with helper._shellng_with:
        helper.main_handler(
            clear_tasks=False,
            task_list=tasks,
            auto_close=False
        )
        helper.my_building()
        helper.clear_daily_task()