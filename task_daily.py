import itertools
import sys
import time
import signal

from Arknights.shell_next import _create_helper

tasks = [("1-7", 99)]

if __name__ == '__main__':

    helper = _create_helper()
    with helper._shellng_with:
        helper.my_building()
        helper.main_handler(
            clear_tasks=False,
            task_list=tasks,
            auto_close=False
        )
        helper.clear_daily_task()