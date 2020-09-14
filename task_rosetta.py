import itertools
import sys
import time
import signal

from Arknights.shell_next import _create_helper

if __name__ == '__main__':

    helper = _create_helper()
    with helper._shellng_with:
        helper.my_building()
        helper.clear_daily_task()