import itertools
import sys
import time
import signal

from Arknights.shell_next import _create_helper

tasks_1_7 = [("1-7", 99)]
tasks_ce_5 = [("CE-5", 99)]

if __name__ == '__main__':

    weekDay = time.ctime()[:3]
    curHour = int(time.ctime()[-13:-11])
    do_ce_5 = weekDay == "Tue" or weekDay == "Thu" or weekDay == "Sat" or weekDay == "Sun"

    do_battle = sys.argv[1] != "nobattle" if len(sys.argv) > 1 else True

    helper = _create_helper()
    helper.use_refill = True
    helper.refill_with_item = True
    with helper._shellng_with:
        if do_battle:
            if do_ce_5:
                helper.main_handler(
                    clear_tasks=False,
                    task_list=tasks_ce_5,
                    auto_close=False
                )
            helper.main_handler(
                clear_tasks=False,
                task_list=tasks_1_7,
                auto_close=False
            )
        helper.my_building()
        helper.recruit_daily()
        helper.get_credit()
        helper.use_credit()
        helper.clear_daily_task()
