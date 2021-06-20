﻿import os
import sys
import json
import re
import time
import requests
import logging
from typing import Callable
from dataclasses import dataclass
from random import randint, uniform, gauss
from time import sleep, monotonic
from fractions import Fraction

import coloredlogs
import numpy as np

import config
import imgreco
import imgreco.imgops
import penguin_stats.reporter
from connector.ADBConnector import ADBConnector, ensure_adb_alive
from . import stage_path
from Arknights.click_location import *
from Arknights.flags import *
from util.exc_guard import guard
from util.richlog import get_logger

logger = logging.getLogger('helper')
recruit_logger = get_logger('recruit_result')
coloredlogs.install(
    fmt=' Ξ %(message)s',
    #fmt=' %(asctime)s ! %(funcName)s @ %(filename)s:%(lineno)d ! %(levelname)s # %(message)s',
    datefmt='%H:%M:%S',
    level_styles={'warning': {'color': 'green'}, 'error': {'color': 'red'}},
    level='INFO')


def item_name_guard(item):
    return str(item) if item is not None else '<无法识别的物品>'


def item_qty_guard(qty):
    return str(qty) if qty is not None else '?'

def send_message(msg):
    requests.post(
            "https://api.alertover.com/v1/alert",
            data={
                "source": "s-e91f93fc-40d7-4f1c-bdae-7de229d7",
                "receiver": "g-4bb5ab90-25a9-4ab3-936f-91a6363f",
                "content": msg
            }
        )

def format_recoresult(recoresult):
    result = None
    with guard(logger):
        result = '[%s] %s' % (recoresult['operation'],
            '; '.join('%s: %s' % (grpname, ', '.join('%sx%s' % (item_name_guard(itemtup[0]), item_qty_guard(itemtup[1]))
            for itemtup in grpcont))
            for grpname, grpcont in recoresult['items']))
    if result is None:
        result = '<发生错误>'
    return result


class ArknightsHelper(object):
    def __init__(self, adb_host=None, device_connector=None):  # 当前绑定到的设备
        ensure_adb_alive()
        if device_connector is not None:
            self.adb = device_connector
        else:
            self.adb = ADBConnector(adb_serial=adb_host)
        self.viewport = self.adb.screen_size
        self.operation_time = []
        self.delay_impl = sleep
        if DEBUG_LEVEL >= 1:
            self.__print_info()
        self.refill_with_item = config.get('behavior/refill_ap_with_item', False)
        self.refill_with_item_close_time_only = False
        self.refill_with_originium = config.get('behavior/refill_ap_with_originium', False)
        self.use_refill = self.refill_with_item or self.refill_with_originium
        self.loots = {}
        self.use_penguin_report = config.get('reporting/enabled', False)
        if self.use_penguin_report:
            self.penguin_reporter = penguin_stats.reporter.PenguinStatsReporter()
        self.refill_count = 0
        self.max_refill_count = None
        if Fraction(self.viewport[0], self.viewport[1]) < Fraction(16, 9):
            logger.warn('当前分辨率（%dx%d）不符合要求', self.viewport[0], self.viewport[1])
            if Fraction(self.viewport[1], self.viewport[0]) >= Fraction(16, 9):
                logger.info('屏幕截图可能需要旋转，请尝试在 device-config 中指定旋转角度')
                img = self.adb.screenshot()
                imgfile = os.path.join(config.SCREEN_SHOOT_SAVE_PATH, 'orientation-diagnose-%s.png' % time.strftime("%Y%m%d-%H%M%S"))
                img.save(imgfile)
                import json
                logger.info('参考 %s 以更正 device-config.json[%s]["screenshot_rotate"]', imgfile, json.dumps(self.adb.config_key))

        logger.debug("成功初始化模块")

    def __print_info(self):
        logger.info('当前系统信息:')
        logger.info('ADB 服务器:\t%s:%d', *config.ADB_SERVER)
        logger.info('分辨率:\t%dx%d', *self.viewport)
        # logger.info('OCR 引擎:\t%s', ocr.engine.info)
        logger.info('截图路径:\t%s', config.SCREEN_SHOOT_SAVE_PATH)

        if config.enable_baidu_api:
            logger.info('%s',
                        """百度API配置信息:
        APP_ID\t{app_id}
        API_KEY\t{api_key}
        SECRET_KEY\t{secret_key}
                        """.format(
                            app_id=config.APP_ID, api_key=config.API_KEY, secret_key=config.SECRET_KEY
                        )
                        )

    def __del(self):
        self.adb.run_device_cmd("am force-stop {}".format(config.ArkNights_PACKAGE_NAME))

    def destroy(self):
        self.__del()

    def check_game_active(self):  # 启动游戏 需要手动调用
        logger.debug("helper.check_game_active")
        current = self.adb.run_device_cmd('dumpsys window windows | grep mCurrentFocus').decode(errors='ignore')
        logger.debug("正在尝试启动游戏")
        logger.debug(current)
        if config.ArkNights_PACKAGE_NAME in current:
            logger.debug("游戏已启动")
        else:
            self.adb.run_device_cmd(
                "am start -n {}/{}".format(config.ArkNights_PACKAGE_NAME, config.ArkNights_ACTIVITY_NAME))
            logger.debug("成功启动游戏")

    def __wait(self, n=10,  # 等待时间中值
               MANLIKE_FLAG=True):  # 是否在此基础上设偏移量
        if MANLIKE_FLAG:
            m = uniform(0, 0.3)
            n = uniform(n - m * 0.5 * n, n + m * n)
        self.delay_impl(n)

    def mouse_click(self,  # 点击一个按钮
                    XY):  # 待点击的按钮的左上和右下坐标
        assert (self.viewport == (1280, 720))
        logger.debug("helper.mouse_click")
        xx = randint(XY[0][0], XY[1][0])
        yy = randint(XY[0][1], XY[1][1])
        logger.info("接收到点击坐标并传递xx:{}和yy:{}".format(xx, yy))
        self.adb.touch_tap((xx, yy))
        self.__wait(TINY_WAIT, MANLIKE_FLAG=True)

    def tap_rect(self, rc):
        hwidth = (rc[2] - rc[0]) / 2
        hheight = (rc[3] - rc[1]) / 2
        midx = rc[0] + hwidth
        midy = rc[1] + hheight
        xdiff = max(-1, min(1, gauss(0, 0.2)))
        ydiff = max(-1, min(1, gauss(0, 0.2)))
        tapx = int(midx + xdiff * hwidth)
        tapy = int(midy + ydiff * hheight)
        self.adb.touch_tap((tapx, tapy))
        self.__wait(TINY_WAIT, MANLIKE_FLAG=True)

    def tap_quadrilateral(self, pts):
        pts = np.asarray(pts)
        acdiff = max(0, min(2, gauss(1, 0.2)))
        bddiff = max(0, min(2, gauss(1, 0.2)))
        halfac = (pts[2] - pts[0]) / 2
        m = pts[0] + halfac * acdiff
        pt2 = pts[1] if bddiff > 1 else pts[3]
        halfvec = (pt2 - m) / 2
        finalpt = m + halfvec * bddiff
        self.adb.touch_tap(tuple(int(x) for x in finalpt))
        self.__wait(TINY_WAIT, MANLIKE_FLAG=True)

    def wait_for_still_image(self, threshold=16, crop=None, timeout=60, raise_for_timeout=True, check_delay=1):
        if crop is None:
            shooter = lambda: self.adb.screenshot(False)
        else:
            shooter = lambda: self.adb.screenshot(False).crop(crop)
        screenshot = shooter()
        t0 = time.monotonic()
        ts = t0 + timeout
        n = 0
        minerr = 65025
        message_shown = False
        while (t1 := time.monotonic()) < ts:
            if check_delay > 0:
                self.__wait(check_delay, False)
            screenshot2 = shooter()
            mse = imgreco.imgops.compare_mse(screenshot, screenshot2)
            if mse <= threshold:
                return screenshot2
            screenshot = screenshot2
            if mse < minerr:
                minerr = mse
            if not message_shown and t1-t0 > 10:
                logger.info("等待画面静止")
        if raise_for_timeout:
            raise RuntimeError("%d 秒内画面未静止，最小误差=%d，阈值=%d" % (timeout, minerr, threshold))
        return None

    def module_login(self):
        logger.debug("helper.module_login")
        logger.info("发送坐标LOGIN_QUICK_LOGIN: {}".format(CLICK_LOCATION['LOGIN_QUICK_LOGIN']))
        self.mouse_click(CLICK_LOCATION['LOGIN_QUICK_LOGIN'])
        self.__wait(BIG_WAIT)
        logger.info("发送坐标LOGIN_START_WAKEUP: {}".format(CLICK_LOCATION['LOGIN_START_WAKEUP']))
        self.mouse_click(CLICK_LOCATION['LOGIN_START_WAKEUP'])
        self.__wait(BIG_WAIT)

    def module_battle_slim(self,
                           c_id=None,  # 待战斗的关卡编号
                           set_count=1000,  # 战斗次数
                           check_ai=True,  # 是否检查代理指挥
                           **kwargs):  # 扩展参数:
        '''
        :param sub 是否为子程序 (是否为module_battle所调用)
        :param auto_close 是否自动关闭, 默认为 false
        :param self_fix 是否尝试自动修复, 默认为 false
        :param MAX_TIME 最大检查轮数, 默认在 config 中设置,
            每隔一段时间进行一轮检查判断作战是否结束
            建议自定义该数值以便在出现一定失误,
            超出最大判断次数后有一定的自我修复能力
        :return:
            True 完成指定次数的作战
            False 理智不足, 退出作战
        '''
        logger.debug("helper.module_battle_slim")
        sub = kwargs["sub"] \
            if "sub" in kwargs else False
        auto_close = kwargs["auto_close"] \
            if "auto_close" in kwargs else False
        if set_count == 0:
            return c_id, 0
        self.operation_time = []
        count = 0
        remain = 0
        try:
            for count in range(set_count):
                # logger.info("开始第 %d 次战斗", count + 1)
                self.operation_once_statemachine(c_id, )
                logger.info("第 %d 次作战完成", count + 1)
                if count != set_count - 1:
                    # 2019.10.06 更新逻辑后，提前点击后等待时间包括企鹅物流
                    if config.reporter:
                        self.__wait(SMALL_WAIT, MANLIKE_FLAG=True)
                    else:
                        self.__wait(BIG_WAIT, MANLIKE_FLAG=True)
        except StopIteration:
            logger.error('未能进行第 %d 次作战', count + 1)
            remain = set_count - count
            if remain - 1 > 0:
                logger.error('已忽略余下的 %d 次战斗', remain - 1)

        return c_id, remain


    def can_perform_refill(self):
        if not self.use_refill:
            return False
        if self.max_refill_count is not None:
            return self.refill_count < self.max_refill_count
        else:
            return True

    @dataclass
    class operation_once_state:
        state: Callable = None
        stop: bool = False
        operation_start: float = 0
        first_wait: bool = True
        mistaken_delegation: bool = False
        prepare_reco: dict = None

    def operation_once_statemachine(self, c_id):
        smobj = ArknightsHelper.operation_once_state()
        def on_prepare(smobj):
            count_times = 0
            while True:
                screenshot = self.adb.screenshot()
                recoresult = imgreco.before_operation.recognize(screenshot)
                if recoresult is not None:
                    logger.debug('当前画面关卡：%s', recoresult['operation'])
                    if c_id is not None:
                        # 如果传入了关卡 ID，检查识别结果
                        if recoresult['operation'] != c_id:
                            logger.error('不在关卡界面')
                            raise StopIteration()
                    break
                else:
                    count_times += 1
                    self.__wait(1, False)
                    if count_times <= 7:
                        logger.warning('不在关卡界面')
                        self.__wait(TINY_WAIT, False)
                        continue
                    else:
                        logger.error('{}次检测后都不再关卡界面，退出进程'.format(count_times))
                        raise StopIteration()

            self.CURRENT_STRENGTH = int(recoresult['AP'].split('/')[0])
            ap_text = '理智' if recoresult['consume_ap'] else '门票'
            logger.info('当前%s %d, 关卡消耗 %d', ap_text, self.CURRENT_STRENGTH, recoresult['consume'])
            if self.CURRENT_STRENGTH < int(recoresult['consume']):
                logger.error(ap_text + '不足 无法继续')
                if recoresult['consume_ap'] and self.can_perform_refill():
                    logger.info('尝试回复理智')
                    self.tap_rect(imgreco.before_operation.get_start_operation_rect(self.viewport))
                    self.__wait(SMALL_WAIT)
                    screenshot = self.adb.screenshot()
                    refill_type = imgreco.before_operation.check_ap_refill_type(screenshot)
                    confirm_refill = False
                    if refill_type == 'item' and self.refill_with_item:
                        if not self.refill_with_item_close_time_only or imgreco.common.find_target(screenshot, "before_operation/time_close.png"):
                            logger.info('使用道具回复理智')
                            confirm_refill = True
                    if refill_type == 'originium' and self.refill_with_originium:
                        logger.info('碎石回复理智')
                        confirm_refill = True
                    # FIXME: 道具回复量不足时也会尝试使用
                    if confirm_refill:
                        self.tap_rect(imgreco.before_operation.get_ap_refill_confirm_rect(self.viewport))
                        self.refill_count += 1
                        self.__wait(MEDIUM_WAIT)
                        return  # to on_prepare state
                    else:
                        self.screenshot_and_click("before_operation/cancel_refill.png")
                        self.__wait(MEDIUM_WAIT)
                    logger.error('未能回复理智')
                    self.tap_rect(imgreco.before_operation.get_ap_refill_cancel_rect(self.viewport))
                raise StopIteration()

            if not recoresult['delegated']:
                logger.info('设置代理指挥')
                self.tap_rect(imgreco.before_operation.get_delegate_rect(self.viewport))
                return  # to on_prepare state

            logger.info("理智充足 开始行动")
            self.tap_rect(imgreco.before_operation.get_start_operation_rect(self.viewport))
            smobj.prepare_reco = recoresult
            smobj.state = on_troop

        def on_troop(smobj):
            count_times = 0
            while True:
                self.__wait(TINY_WAIT, False)
                screenshot = self.adb.screenshot()
                recoresult = imgreco.before_operation.check_confirm_troop_rect(screenshot)
                if recoresult:
                    logger.info('确认编队')
                    break
                else:
                    count_times += 1
                    if count_times <= 7:
                        logger.warning('等待确认编队')
                        continue
                    else:
                        logger.error('{} 次检测后不再确认编队界面'.format(count_times))
                        raise StopIteration()
            self.tap_rect(imgreco.before_operation.get_confirm_troop_rect(self.viewport))
            smobj.operation_start = monotonic()
            smobj.state = on_operation

        def on_operation(smobj):
            if smobj.first_wait:
                if len(self.operation_time) == 0:
                    wait_time = BATTLE_NONE_DETECT_TIME
                else:
                    wait_time = sum(self.operation_time) / len(self.operation_time) - 7
                logger.info('等待 %d s' % wait_time)
                self.__wait(wait_time, MANLIKE_FLAG=False)
                smobj.first_wait = False
            t = monotonic() - smobj.operation_start

            logger.info('已进行 %.1f s，判断是否结束', t)

            screenshot = self.adb.screenshot()
            if imgreco.end_operation.check_level_up_popup(screenshot):
                logger.info("等级提升")
                self.operation_time.append(t)
                smobj.state = on_level_up_popup
                return

            if smobj.prepare_reco['consume_ap']:
                tar = imgreco.common.find_target(screenshot, "end_operation/recordtime.png", 0.9, False)
                if tar:
                    logger.info('剿灭战斗结束')
                    self.operation_time.append(t)
                    self.tap_rect(tar)
                    self.__wait(MEDIUM_WAIT)
                    screenshot = self.adb.screenshot()
                    if imgreco.common.find_target(screenshot, "end_operation/weeklyreport.png"):
                        smobj.state = on_end_operation
                        return

            if smobj.prepare_reco['consume_ap']:
                detector = imgreco.end_operation.check_end_operation
            else:
                detector = imgreco.end_operation.check_end_operation_alt
            if detector(screenshot):
                logger.info('战斗结束')
                self.operation_time.append(t)
                crop = imgreco.end_operation.get_still_check_rect(self.viewport)
                if self.wait_for_still_image(crop=crop, timeout=15, raise_for_timeout=True):
                    smobj.state = on_end_operation
                return
            dlgtype, ocrresult = imgreco.common.recognize_dialog(screenshot)
            if dlgtype is not None:
                if dlgtype == 'yesno' and '代理指挥' in ocrresult:
                    logger.warning('代理指挥出现失误')
                    smobj.mistaken_delegation = True
                    if config.get('behavior/mistaken_delegation/settle', False):
                        logger.info('以 2 星结算关卡')
                        self.tap_rect(imgreco.common.get_dialog_right_button_rect(screenshot))
                        self.__wait(2)
                        smobj.stop = True
                        return
                    else:
                        logger.info('放弃关卡')
                        self.tap_rect(imgreco.common.get_dialog_left_button_rect(screenshot))
                        # 关闭失败提示
                        self.wait_for_still_image()
                        self.tap_rect(imgreco.common.get_reward_popup_dismiss_rect(screenshot))
                        # FIXME: 理智返还
                        self.__wait(1)
                        smobj.stop = True
                        return
                elif dlgtype == 'yesno' and '将会恢复' in ocrresult:
                    logger.info('发现放弃行动提示，关闭')
                    self.tap_rect(imgreco.common.get_dialog_left_button_rect(screenshot))
                else:
                    logger.error('未处理的对话框：[%s] %s', dlgtype, ocrresult)
                    raise RuntimeError('unhandled dialog')

            logger.info('战斗未结束')
            self.__wait(BATTLE_FINISH_DETECT)

        def on_level_up_popup(smobj):
            self.__wait(SMALL_WAIT, MANLIKE_FLAG=True)
            logger.info('关闭升级提示')
            self.tap_rect(imgreco.end_operation.get_dismiss_level_up_popup_rect(self.viewport))
            self.wait_for_still_image()
            smobj.state = on_end_operation

        def on_end_operation(smobj):
            screenshot = self.adb.screenshot()
            logger.info('离开结算画面')
            self.tap_rect(imgreco.end_operation.get_dismiss_end_operation_rect(self.viewport))
            reportresult = penguin_stats.reporter.ReportResult.NotReported
            try:
                # 掉落识别
                drops = imgreco.end_operation.recognize(screenshot)
                logger.debug('%s', repr(drops))
                logger.info('掉落识别结果：%s', format_recoresult(drops))
                log_total = len(self.loots)
                for _, group in drops['items']:
                    for name, qty in group:
                        if name is not None and qty is not None:
                            self.loots[name] = self.loots.get(name, 0) + qty
                if log_total:
                    self.log_total_loots()
                if self.use_penguin_report:
                    reportresult = self.penguin_reporter.report(drops)
                    if isinstance(reportresult, penguin_stats.reporter.ReportResult.Ok):
                        logger.debug('report hash = %s', reportresult.report_hash)
            except Exception as e:
                logger.error('', exc_info=True)
            if self.use_penguin_report and reportresult is penguin_stats.reporter.ReportResult.NotReported:
                filename = os.path.join(config.SCREEN_SHOOT_SAVE_PATH, '未上报掉落-%d.png' % time.time())
                with open(filename, 'wb') as f:
                    screenshot.save(f, format='PNG')
                logger.error('未上报掉落截图已保存到 %s', filename)
            smobj.stop = True

        smobj.state = on_prepare
        smobj.stop = False
        smobj.operation_start = 0

        while not smobj.stop:
            oldstate = smobj.state
            smobj.state(smobj)
            if smobj.state != oldstate:
                logger.debug('state changed to %s', smobj.state.__name__)

        if smobj.mistaken_delegation and config.get('behavior/mistaken_delegation/skip', True):
            raise StopIteration()


    def back_to_main(self):  # 回到主页
        logger.info("正在返回主页")
        retry_count = 0
        max_retry = 3
        while True:
            screenshot = self.adb.screenshot()

            if imgreco.main.check_main(screenshot):
                break

            # 检查是否有返回按钮
            if imgreco.common.check_nav_button(screenshot):
                logger.info('发现返回按钮，点击返回')
                self.tap_rect(imgreco.common.get_nav_button_back_rect(self.viewport))
                self.__wait(SMALL_WAIT)
                # 点击返回按钮之后重新检查
                continue

            if imgreco.common.check_get_item_popup(screenshot):
                logger.info('当前为获得物资画面，关闭')
                self.tap_rect(imgreco.common.get_reward_popup_dismiss_rect(self.viewport))
                self.__wait(SMALL_WAIT)
                continue

            # 检查是否在设置画面
            if imgreco.common.check_setting_scene(screenshot):
                logger.info("当前为设置/邮件画面，返回")
                self.tap_rect(imgreco.common.get_setting_back_rect(self.viewport))
                self.__wait(SMALL_WAIT)
                continue

            # 检测是否有关闭按钮
            rect, confidence = imgreco.common.find_close_button(screenshot)
            if confidence > 0.9:
                logger.info("发现关闭按钮")
                self.tap_rect(rect)
                self.__wait(SMALL_WAIT)
                continue

            dlgtype, ocr = imgreco.common.recognize_dialog(screenshot)
            if dlgtype == 'yesno':
                if '基建' in ocr or '停止招募' in ocr:
                    self.tap_rect(imgreco.common.get_dialog_right_button_rect(screenshot))
                    self.__wait(5)
                    continue
                elif '好友列表' in ocr: 
                    self.tap_rect(imgreco.common.get_dialog_right_button_rect(screenshot))
                    self.__wait(7)
                    continue
                elif '招募干员' in ocr or '加急' in ocr:
                    self.tap_rect(imgreco.common.get_dialog_left_button_rect(screenshot))
                    self.__wait(3)
                    continue
                else:
                    raise RuntimeError('未适配的对话框')
            elif dlgtype == 'ok':
                self.tap_rect(imgreco.common.get_dialog_ok_button_rect(screenshot))
                self.__wait(1)
                continue
            retry_count += 1
            if retry_count > max_retry:
                filename = os.path.join(config.SCREEN_SHOOT_SAVE_PATH, '未知画面-%d.png' % time.time())
                with open(filename, 'wb') as f:
                    screenshot.save(f, format='PNG')

                raise RuntimeError('未知画面')
        logger.info("已回到主页")

    def module_battle(self,  # 完整的战斗模块
                      c_id,  # 选择的关卡
                      set_count=1000):  # 作战次数
        logger.debug("helper.module_battle")
        c_id = c_id.upper()
        if config.get('behavior/use_ocr_goto_stage', False) and stage_path.is_stage_supported_ocr(c_id):
            self.goto_stage_by_ocr(c_id)
        elif stage_path.is_stage_supported(c_id):
            self.goto_stage(c_id)
        else:
            logger.error('不支持的关卡：%s', c_id)
            raise ValueError(c_id)
        return self.module_battle_slim(c_id,
                                set_count=set_count,
                                check_ai=True,
                                sub=True)

    def main_handler(self, task_list, clear_tasks=False, auto_close=True):
        if len(task_list) == 0:
            logger.fatal("任务清单为空!")

        logger.info(task_list)

        for c_id, count in task_list:
            # if not stage_path.is_stage_supported(c_id):
            #     raise ValueError(c_id)
            logger.info("开始 %s", c_id)
            flag = self.module_battle(c_id, count)

        logger.info("任务清单执行完毕")

    def clear_task(self):
        logger.debug("helper.clear_task")
        logger.info("领取每日任务")
        self.back_to_main()
        screenshot = self.adb.screenshot()
        logger.info('进入任务界面')
        self.tap_quadrilateral(imgreco.main.get_task_corners(screenshot))
        self.__wait(SMALL_WAIT)
        screenshot = self.adb.screenshot()

        hasbeginner = imgreco.task.check_beginners_task(screenshot)
        if hasbeginner:
            logger.info('发现见习任务，切换到每日任务')
            self.tap_rect(imgreco.task.get_daily_task_rect(screenshot, hasbeginner))
            self.__wait(TINY_WAIT)
            screenshot = self.adb.screenshot()
        self.clear_task_worker()
        logger.info('切换到每周任务') #默认进入见习任务或每日任务，因此无需检测，直接切换即可
        self.tap_rect(imgreco.task.get_weekly_task_rect(screenshot, hasbeginner))
        self.clear_task_worker()

    def clear_task_worker(self):
        screenshot = self.adb.screenshot()
        kickoff = True
        while True:
            if imgreco.common.check_nav_button(screenshot) and not imgreco.task.check_collectable_reward(screenshot):
                logger.info("奖励已领取完毕")
                break
            if kickoff:
                logger.info('开始领取奖励')
                kickoff = False
            self.tap_rect(imgreco.task.get_collect_reward_button_rect(self.viewport))
            screenshot = self.adb.screenshot(cached=False)

    def recruit(self):
        from . import recruit_calc
        logger.info('识别招募标签')
        tags = imgreco.recruit.get_recruit_tags(self.adb.screenshot())
        logger.info('可选标签：%s', ' '.join(tags))
        result = recruit_calc.calculate(tags)
        logger.debug('计算结果：%s', repr(result))
        return result

    def recruit_add(self):
        from . import recruit_calc

        screenshot = self.adb.screenshot()
        tar = imgreco.common.find_target(screenshot, "recruit/start.png", 0.7)
        if tar:
            self.tap_rect(tar)
        else:
            return False

        max_refresh_num = 3
        while max_refresh_num >= 0:
            tags, tags_pos = imgreco.recruit.get_recruit_tags(self.adb.screenshot())
            logger.info('可选标签：%s', ' '.join(tags))
            try:
                result = recruit_calc.calculate(tags)
            except Exception as e:
                self.__wait(SMALL_WAIT)
                tags, tags_pos = imgreco.recruit.get_recruit_tags(self.adb.screenshot())
                logger.info('可选标签：%s', ' '.join(tags))
                try:
                    result = recruit_calc.calculate(tags)
                except:
                    send_message("无法识别标签: " +  ' '.join(tags))
                    return False

            if any('资深' in tag for tag in tags):
                logger.info('计算结果：%s', repr(result))
                send_message(' '.join(tags))
                return False

            if result[0][2] > 0:
                break

            if not self.screenshot_and_click("recruit/refresh.png"):
                break
            self.__wait(TINY_WAIT)
            if not self.screenshot_and_click("recruit/red_ok.png"):
                break
            self.__wait(SMALL_WAIT)
            max_refresh_num -= 1


        candidate = result[0]
        if result[0][2] == 0:
            filtered_result = list(filter(lambda x: len(x[0]) == 1 and x[2] >= 0, result))
            candidate = filtered_result[randint(0, len(filtered_result)-1)]

        logger.info(candidate)

        for i, tag in enumerate(tags):
            if tag in candidate[0]:
                self.tap_rect(tags_pos[i])

        screenshot = self.adb.screenshot()
        self.tap_rect((384, 280, 516, 320))

        screenshot = self.adb.screenshot()
        tar = imgreco.common.find_target(screenshot, "recruit/time_confirm.png")
        if tar is None:
            send_message("招募时间确认出错")
            return False

        self.screenshot_and_click("recruit/ok.png")
        self.__wait(SMALL_WAIT)

        return True

    def recruit_get(self):

        if not self.screenshot_and_click("recruit/recruit_confirm.png"):
            return False

        self.__wait(SMALL_WAIT)
        self.screenshot_and_click("recruit/skip.png")

        self.__wait(SMALL_WAIT)
        screenshot = self.adb.screenshot()
        recruit_logger.logimage(imgreco.imgops.scale_to_height(screenshot, 240))
        self.tap_rect((525, 415, 750, 445))
        self.__wait(TINY_WAIT)

        return True

    def recruit_daily(self):
        self.back_to_main()
        screenshot = self.adb.screenshot()
        logger.info('进入公开招募界面')
        self.tap_quadrilateral(imgreco.main.get_public_recruit(screenshot))
        self.__wait(SMALL_WAIT)

        while self.recruit_get():
            pass

        recruit_num = 3
        while recruit_num > 0:
            if not self.recruit_add():
                return

            recruit_num -= 1

    def recruit_batched(self, recruit_num = 100):
        while recruit_num > 0:

            if not self.recruit_add():
                return

            self.screenshot_and_click("recruit/recruit_now.png")
            self.__wait(TINY_WAIT)
            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "recruit/red_ok.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
            else:
                return

            if not self.recruit_get():
                return

            recruit_num -= 1


    def find_and_tap(self, partition, target):
        lastpos = None
        while True:
            screenshot = self.adb.screenshot()
            recoresult = imgreco.map.recognize_map(screenshot, partition)
            if recoresult is None:
                # TODO: retry
                logger.error('未能定位关卡地图')
                raise RuntimeError('recognition failed')
            if target in recoresult:
                pos = recoresult[target]
                logger.info('目标 %s 坐标: %s', target, pos)
                if lastpos is not None and tuple(pos) == tuple(lastpos):
                    logger.error('拖动后坐标未改变')
                    raise RuntimeError('拖动后坐标未改变')
                if 0 < pos[0] < self.viewport[0]:
                    logger.info('目标在可视区域内，点击')
                    self.adb.touch_tap(pos, offsets=(5, 5))
                    self.__wait(3)
                    break
                else:
                    lastpos = pos
                    originX = self.viewport[0] // 2 + randint(-100, 100)
                    originY = self.viewport[1] // 2 + randint(-100, 100)
                    if pos[0] < 0:  # target in left of viewport
                        logger.info('目标在可视区域左侧，向右拖动')
                        # swipe right
                        diff = -pos[0]
                        if abs(diff) < 100:
                            diff = 120
                        diff = min(diff, self.viewport[0] - originX)
                    elif pos[0] > self.viewport[0]:  # target in right of viewport
                        logger.info('目标在可视区域右侧，向左拖动')
                        # swipe left
                        diff = self.viewport[0] - pos[0]
                        if abs(diff) < 100:
                            diff = -120
                        diff = max(diff, -originX)
                    self.adb.touch_swipe2((originX, originY), (diff * 0.7 * uniform(0.8, 1.2), 0), max(250, diff / 2))
                    self.__wait(5)
                    continue

            else:
                raise KeyError((target, partition))

    def find_and_tap_episode_by_ocr(self, target):
        import imgreco.stage_ocr
        from resources.imgreco.map_vectors import ep2region, region2ep
        target_region = ep2region.get(target)
        if target_region is None:
            logger.error(f'未能定位章节区域, target: {target}')
            raise RuntimeError('recognition failed')
        vw, vh = imgreco.util.get_vwvh(self.viewport)
        episode_tag_rect = tuple(map(int, (35.185*vh, 39.259*vh, 50.093*vh, 43.056*vh)))
        next_ep_region_rect = (5.833*vh, 69.167*vh, 11.944*vh, 74.815*vh)
        prev_ep_region_rect = (5.833*vh, 15.370*vh, 11.944*vh, 21.481*vh)
        current_ep_rect = (50*vw+19.907*vh, 28.426*vh, 50*vw+63.426*vh, 71.944*vh)
        episode_move = (400 * self.viewport[1] / 1080)

        while True:
            screenshot = self.adb.screenshot()
            current_episode_tag = screenshot.crop(episode_tag_rect)
            current_episode_str = imgreco.stage_ocr.do_img_ocr(current_episode_tag)
            logger.info(f'当前章节: {current_episode_str}')
            if not current_episode_str.startswith('EPISODE'):
                logger.error(f'章节识别失败, current_episode_str: {current_episode_str}')
                raise RuntimeError('recognition failed')
            current_episode = int(current_episode_str[7:])
            current_region = ep2region.get(current_episode)
            if current_region is None:
                logger.error(f'未能定位章节区域, current_episode: {current_episode}')
                raise RuntimeError('recognition failed')
            if current_region == target_region:
                break
            if current_region > target_region:
                logger.info(f'前往上一章节区域')
                self.tap_rect(prev_ep_region_rect)
            else:
                logger.info(f'前往下一章节区域')
                self.tap_rect(next_ep_region_rect)
        while current_episode != target:
            move = min(abs(current_episode - target), 2) * episode_move * (1 if current_episode > target else -1)
            self.__swipe_screen(move, 10, self.viewport[0] // 4 * 3)
            screenshot = self.adb.screenshot()
            current_episode_tag = screenshot.crop(episode_tag_rect)
            current_episode_str = imgreco.stage_ocr.do_img_ocr(current_episode_tag)
            current_episode = int(current_episode_str[7:])
            logger.info(f'当前章节: {current_episode_str}')

        self.tap_rect(current_ep_rect)

    def find_and_tap_stage_by_ocr(self, partition, target, partition_map=None):
        import imgreco.stage_ocr
        target = target.upper()
        if partition_map is None:
            from resources.imgreco.map_vectors import stage_maps_linear
            partition_map = stage_maps_linear[partition]
        target_index = partition_map.index(target)
        while True:
            screenshot = self.adb.screenshot()
            tags_map = imgreco.stage_ocr.recognize_all_screen_stage_tags(screenshot)
            logger.debug('tags map: ' + repr(tags_map))
            pos = tags_map.get(target)
            if pos:
                logger.info('目标在可视区域内，点击')
                self.adb.touch_tap(pos, offsets=(5, 5))
                self.__wait(1)
                return

            known_indices = [partition_map.index(x) for x in tags_map.keys() if x in partition_map]

            originX = self.viewport[0] // 2 + randint(-100, 100)
            originY = self.viewport[1] // 2 + randint(-100, 100)
            move = randint(self.viewport[0] // 4, self.viewport[0] // 3)

            if all(x > target_index for x in known_indices):
                logger.info('目标在可视区域左侧，向右拖动')
            elif all(x < target_index for x in known_indices):
                move = -move
                logger.info('目标在可视区域右侧，向左拖动')
            else:
                logger.error('未能定位关卡地图')
                raise RuntimeError('recognition failed')
            self.adb.touch_swipe2((originX, originY), (move, max(250, move // 2)))
            self.__wait(1)

    def find_and_tap_daily(self, partition, target, *, recursion=0):
        screenshot = self.adb.screenshot()
        recoresult = imgreco.map.recognize_daily_menu(screenshot, partition)
        if target in recoresult:
            pos, conf = recoresult[target]
            logger.info('目标 %s 坐标=%s 差异=%f', target, pos, conf)
            offset = self.viewport[1] * 0.12  ## 24vh * 24vh range
            self.tap_rect((*(pos - offset), *(pos + offset)))
        else:
            if recursion == 0:
                logger.info('目标可能在可视区域右侧，向左拖动')
                originX = self.viewport[0] // 2 + randint(-100, 100)
                originY = self.viewport[1] // 2 + randint(-100, 100)
                self.adb.touch_swipe2((originX, originY), (-self.viewport[0] * 0.2, 0), 400)
                self.__wait(2)
                self.find_and_tap_daily(partition, target, recursion=recursion+1)
            else:
                logger.error('未找到目标，是否未开放关卡？')

    def goto_stage(self, stage):
        if not stage_path.is_stage_supported(stage):
            logger.error('不支持的关卡：%s', stage)
            raise ValueError(stage)
        path = stage_path.get_stage_path(stage)
        self.back_to_main()
        logger.info('进入作战')
        self.tap_quadrilateral(imgreco.main.get_ballte_corners(self.adb.screenshot()))
        self.__wait(3)
        if path[0] == 'main':
            self.find_and_tap('episodes', path[1])
            self.find_and_tap(path[1], path[2])
        elif path[0] == 'material' or path[0] == 'soc':
            logger.info('选择类别')
            self.tap_rect(imgreco.map.get_daily_menu_entry(self.viewport, path[0]))
            self.find_and_tap_daily(path[0], path[1])
            self.find_and_tap(path[1], path[2])
        else:
            raise NotImplementedError()

    def goto_stage_by_ocr(self, stage):
        path = stage_path.get_stage_path(stage)
        self.back_to_main()
        logger.info('进入作战')
        self.tap_quadrilateral(imgreco.main.get_ballte_corners(self.adb.screenshot()))
        self.__wait(TINY_WAIT)
        if path[0] == 'main':
            vw, vh = imgreco.util.get_vwvh(self.viewport)
            self.tap_rect((14.316*vw, 89.815*vh, 28.462*vw, 99.815*vh))
            self.find_and_tap_episode_by_ocr(int(path[1][2:]))
            self.find_and_tap_stage_by_ocr(path[1], path[2])
        elif path[0] == 'material' or path[0] == 'soc':
            logger.info('选择类别')
            self.tap_rect(imgreco.map.get_daily_menu_entry(self.viewport, path[0]))
            self.find_and_tap_daily(path[0], path[1])
            self.find_and_tap(path[1], path[2])
        else:
            raise NotImplementedError()

    def repeat_last_stage(self, stages, count):
        import imgreco.stage_ocr
        self.back_to_main()
        logger.info('进入上次作战')
        self.tap_quadrilateral(imgreco.main.get_ballte_corners(self.adb.screenshot()))
        self.__wait(TINY_WAIT)

        screenshot = self.adb.screenshot()
        content = screenshot.crop((1160, 568, 1258, 604))
        stage_id = imgreco.stage_ocr.do_img_ocr(content)
        logger.info(stage_id)

        if stage_id not in stages:
            send_message("当前关卡为 %s, 取消作战" % stage_id)
            return

        self.tap_rect((1160, 568, 1258, 604))
        self.module_battle_slim(
            c_id=None,
            set_count=count,
        )

    def get_credit(self):
        logger.debug("helper.get_credit")
        logger.info("领取信赖")
        self.back_to_main()
        screenshot = self.adb.screenshot()
        logger.info('进入好友列表')
        self.tap_quadrilateral(imgreco.main.get_friend_corners(screenshot))
        self.__wait(SMALL_WAIT)
        self.tap_quadrilateral(imgreco.main.get_friend_list(screenshot))
        self.__wait(SMALL_WAIT)
        logger.info('访问好友基建')
        self.tap_quadrilateral(imgreco.main.get_friend_build(screenshot))
        self.__wait(MEDIUM_WAIT)
        building_count = 0
        while self.screenshot_and_click("credit/next_friend.png"):
            self.__wait(MEDIUM_WAIT)
            building_count = building_count + 1
            logger.info('访问第 %s 位好友', building_count)
        logger.info('信赖领取完毕')

    def use_credit(self):
        self.back_to_main()
        self.screenshot_and_click("main/shop.png")
        self.__wait(SMALL_WAIT)
        self.screenshot_and_click("credit/credit_banner.png")
        self.__wait(TINY_WAIT)

        if self.screenshot_and_click("credit/receive_credit.png"):
            self.__wait(SMALL_WAIT)
            screenshot = self.adb.screenshot()
            if imgreco.common.check_get_item_popup(screenshot):
                self.tap_rect(imgreco.common.get_reward_popup_dismiss_rect(self.viewport))
                self.__wait(SMALL_WAIT)

        screenshot = self.adb.screenshot()
        targets = imgreco.common.find_targets(screenshot, "credit/onsale.png")
        for tar in targets:
            # title = screenshot.crop((tar[0] + 50, tar[1] - 50, tar[0] + 180, tar[1] - 5)).convert('L')
            # recruit_logger.logimage(title)
            self.tap_rect(tar)
            self.__wait(TINY_WAIT)
            self.screenshot_and_click("credit/buy.png")
            self.__wait(SMALL_WAIT)
            screenshot = self.adb.screenshot()
            if imgreco.common.find_target(screenshot, "credit/buy.png"): #still see the buy button means not enough credit
                self.nav_back()
                break

            screenshot = self.adb.screenshot()
            if imgreco.common.check_get_item_popup(screenshot):
                self.tap_rect(imgreco.common.get_reward_popup_dismiss_rect(self.viewport))
                self.__wait(SMALL_WAIT)


    def screenshot_and_click(self, img_path):
        screenshot = self.adb.screenshot()
        tar = imgreco.common.find_target(screenshot, img_path)
        if tar:
            self.tap_rect(tar)
            return True
        else:
            return False

    def wait_and_click(self, img_path, max_wait_time = 20, exit_if_failure = True):
        wait_time = 0
        while wait_time < max_wait_time:
            logger.info('点击 ' + img_path)
            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, img_path)
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
                break
            else:
                self.__wait(MEDIUM_WAIT)
                wait_time += 1

        if exit_if_failure and wait_time >= max_wait_time:
            logger.info('结束任务, 超时 ' + img_path)
            sys.exit(2)

    def test(self):
        screenshot = self.adb.screenshot()
        tar = imgreco.common.find_target(screenshot, "end_operation/recordtime.png")
        if tar:
            self.tap_rect(tar)
            self.__wait(SMALL_WAIT)

        if imgreco.common.find_target(screenshot, "end_operation/weeklyreport.png"):
            logger.info('战斗结束')

            screenshot = self.adb.screenshot()
            logger.info('离开结算画面')
            self.tap_rect(imgreco.end_operation.get_dismiss_end_operation_rect(self.viewport))
            reportresult = penguin_stats.reporter.ReportResult.NotReported
            try:
                # 掉落识别
                drops = imgreco.end_operation.recognize(screenshot)
                logger.debug('%s', repr(drops))
                logger.info('掉落识别结果：%s', format_recoresult(drops))
                log_total = len(self.loots)
                for _, group in drops['items']:
                    for name, qty in group:
                        if name is not None and qty is not None:
                            self.loots[name] = self.loots.get(name, 0) + qty
                if log_total:
                    self.log_total_loots()
                if self.use_penguin_report:
                    reportresult = self.penguin_reporter.report(drops)
                    if isinstance(reportresult, penguin_stats.reporter.ReportResult.Ok):
                        logger.debug('report hash = %s', reportresult.report_hash)
            except Exception as e:
                logger.error('', exc_info=True)
            if self.use_penguin_report and reportresult is penguin_stats.reporter.ReportResult.NotReported:
                filename = os.path.join(config.SCREEN_SHOOT_SAVE_PATH, '未上报掉落-%d.png' % time.time())
                with open(filename, 'wb') as f:
                    screenshot.save(f, format='PNG')
                logger.error('未上报掉落截图已保存到 %s', filename)

    def login(self, username, userpass):
        self.wait_and_click("login/start.png")
        self.__wait(MEDIUM_WAIT)
        self.wait_and_click("login/account.png", max_wait_time = 10, exit_if_failure = False)
        self.__wait(TINY_WAIT)
        self.wait_and_click("login/login.png")
        self.tap_rect((525, 415, 750, 445))
        self.adb.input_text(username)
        self.__wait(SMALL_WAIT, MANLIKE_FLAG=True)
        self.tap_rect((525, 415, 750, 445))
        self.__wait(TINY_WAIT)
        self.tap_rect((525, 468, 750, 498))
        self.adb.input_text(userpass)
        self.__wait(SMALL_WAIT, MANLIKE_FLAG=True)
        self.tap_rect((525, 468, 750, 498))
        self.wait_and_click("login/confirmLogin.png")
        self.__wait(15, MANLIKE_FLAG=True)

    def nav_back(self, wait_time = SMALL_WAIT):
        screenshot = self.adb.screenshot()
        if imgreco.common.check_nav_button(screenshot):
            logger.info('发现返回按钮，点击返回')
            self.tap_rect(imgreco.common.get_nav_button_back_rect(self.viewport))
            self.__wait(wait_time)

    def my_building(self):
        self.back_to_main()
        logger.info('进入我的基建')
        screenshot = self.adb.screenshot()
        self.tap_quadrilateral(imgreco.main.get_back_my_build(screenshot))
        self.__wait(MEDIUM_WAIT + 3)

        screenshot = self.adb.screenshot()
        noti_rect = imgreco.common.find_target(screenshot, "building/notification.png")
        if noti_rect:
            self.tap_rect(noti_rect)
            self.__wait(SMALL_WAIT)
            logger.info('收取制造产物')
            self.tap_quadrilateral(imgreco.main.get_my_build_task_clear(screenshot))
            self.__wait(SMALL_WAIT)
            self.tap_quadrilateral(imgreco.main.get_my_build_task_clear(screenshot))
            self.__wait(SMALL_WAIT)
            self.tap_quadrilateral(imgreco.main.get_my_build_task_clear(screenshot))
            self.__wait(SMALL_WAIT)
            self.tap_rect(noti_rect)
            self.__wait(SMALL_WAIT)

        i = 0
        apartment_finished = False
        while not apartment_finished and i < 4:
            logger.info('进入第%d个宿舍' % (i+1))
            if i == 0:
                self.tap_rect((700, 300, 850, 320))
                self.__wait(SMALL_WAIT)
            else:
                screenshot = self.adb.screenshot()
                targets = imgreco.common.find_targets(screenshot, "building/apartment.png")
                if len(targets) <= i:
                    break

                self.tap_rect(targets[i])
                self.__wait(SMALL_WAIT)

            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/people.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(SMALL_WAIT)
                screenshot = self.adb.screenshot()

            tar = imgreco.common.find_target(screenshot, "building/inrest.png")
            if tar is None:
                self.screenshot_and_click("building/clear.png")
                self.__wait(TINY_WAIT)
                screenshot = self.adb.screenshot()

            tar = imgreco.common.find_target(screenshot, "building/add.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)

                screenshot = self.adb.screenshot()
                charas = imgreco.common.find_targets(screenshot, "building/distracted.png")
                apartment_finished = len(charas) <= 5
                for chara in charas[:5]:
                    self.tap_rect(chara)
                self.screenshot_and_click("building/confirm.png")
                self.__wait(SMALL_WAIT)

            self.nav_back()

            i += 1

        i = 0
        while i < 1:
            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/center.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
            else:
                break

            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/people.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
            elif imgreco.common.find_target(screenshot, "building/people_inverse.png") is None:
                break

            logger.info('进入控制中心')

            screenshot = self.adb.screenshot()
            slots = imgreco.common.find_targets(screenshot, "building/add.png")
            print (len(slots))
            if len(slots) > 3:
                self.tap_rect(slots[0])
                self.__wait(TINY_WAIT)

                screenshot = self.adb.screenshot()
                targets = []

                tar = imgreco.common.find_target(screenshot, "building/buff_center.png")
                if tar:
                    targets.append(tar)

                tar = imgreco.common.find_target(screenshot, "building/buff_center_factory.png")
                if tar:
                    targets.append(tar)

                if len(targets) > 0:
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_center_rainbow.png"))
                
                for target in targets[:5]:
                    self.tap_rect(target)

                self.screenshot_and_click("building/confirm.png")

            self.nav_back(TINY_WAIT)
            i += 1

        drone_used = False
        i = 0
        while i < 4:
            originX = self.viewport[0] // 2 + randint(-100, 100)
            originY = self.viewport[1] // 2 + randint(-100, 100)
            self.adb.touch_swipe2((originX, originY), (100.0 * uniform(0.8, 1.2), 0), 255)
            self.__wait(1)

            screenshot = self.adb.screenshot()
            targets = imgreco.common.find_targets(screenshot, "building/factory.png")
            if len(targets) <= i:
                break

            self.tap_rect(targets[i])
            self.__wait(TINY_WAIT)

            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/people.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
            elif imgreco.common.find_target(screenshot, "building/people_inverse.png") is None:
                continue

            factory_item = None
            screenshot = self.adb.screenshot()
            if imgreco.common.find_target(screenshot, "building/item_gold.png"):
                factory_item = "gold"
            elif imgreco.common.find_target(screenshot, "building/item_record.png"):
                factory_item = "record"
            elif imgreco.common.find_target(screenshot, "building/item_gem.png"):
                factory_item = "gem"

            slots = imgreco.common.find_targets(screenshot, "building/add.png")
            logger.info('进入制造站 ' + factory_item)

            if factory_item and len(slots) > 2:
                self.tap_rect(slots[0])
                self.__wait(TINY_WAIT)

                screenshot = self.adb.screenshot()

                targets = []
                if factory_item == "gold":
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_gold.png"))
                elif factory_item == "record":
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_record.png"))
                elif factory_item == "gem":
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_gem.png"))

                if len(targets) < 3:
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_common.png"))
                if len(targets) < 3:
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_common2.png"))
                if len(targets) < 3:
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_common3.png"))
                if len(targets) < 3:
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_common4.png"))

                if len(targets) < 2:
                    wendy = imgreco.common.find_target(screenshot, "building/buff_wendy.png")
                    if wendy:
                        targets = [wendy]

                for target in targets[:3]:
                    self.tap_rect(target)
                self.screenshot_and_click("building/confirm.png")

            #fulfill
            if factory_item == "gem":
                screenshot = self.adb.screenshot()
                tar = imgreco.common.find_target(screenshot, "building/item_gem.png")
                if tar:
                    self.tap_rect(tar)
                    self.__wait(TINY_WAIT)
                    self.tap_rect(tar)
                    self.__wait(SMALL_WAIT)
                    self.screenshot_and_click("building/most.png")
                    self.__wait(TINY_WAIT)
                    self.screenshot_and_click("building/confirmIcon.png")
                    self.__wait(SMALL_WAIT)
                    self.screenshot_and_click("building/accelerate.png")
                    self.__wait(TINY_WAIT)
                    self.screenshot_and_click("building/most_accelerate.png")
                    self.__wait(TINY_WAIT)
                    self.screenshot_and_click("building/confirm_accelerate.png")
                    self.__wait(SMALL_WAIT)
                    self.screenshot_and_click("building/achieve.png")
                    self.__wait(TINY_WAIT)
                    self.nav_back(TINY_WAIT)
                    drone_used = True

            self.nav_back(TINY_WAIT)

            i += 1

        i = 0
        while i < 2:
            originX = self.viewport[0] // 2 + randint(-100, 100)
            originY = self.viewport[1] // 2 + randint(-100, 100)
            self.adb.touch_swipe2((originX, originY), (100.0 * uniform(0.8, 1.2), 0), 255)
            self.__wait(1)

            screenshot = self.adb.screenshot()
            targets = imgreco.common.find_targets(screenshot, "building/trader.png")
            if len(targets) <= i:
                break

            self.tap_rect(targets[i])
            self.__wait(TINY_WAIT)

            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/people.png")
            if tar:
                self.tap_rect(tar)
            elif imgreco.common.find_target(screenshot, "building/people_inverse.png") is None:
                continue

            logger.info('进入贸易站')

            screenshot = self.adb.screenshot()
            slots = imgreco.common.find_targets(screenshot, "building/add.png")
            if len(slots) > 2:
                self.tap_rect(slots[0])
                self.__wait(TINY_WAIT)

                screenshot = self.adb.screenshot()

                targets = []

                tar_texas = imgreco.common.find_target(screenshot, "building/chara_texas.png")
                tar_lappland = imgreco.common.find_target(screenshot, "building/chara_lappland.png")
                tar_exusiai = imgreco.common.find_target(screenshot, "building/chara_exusiai.png")
                if tar_texas and tar_lappland and tar_exusiai:
                    targets = [tar_texas, tar_lappland, tar_exusiai]
                else:
                    targets.extend(imgreco.common.find_targets(screenshot, "building/buff_trader1.png"))
                    if len(targets) < 3:
                        targets.extend(imgreco.common.find_targets(screenshot, "building/buff_trader2.png"))
                    if len(targets) < 3:
                        targets.extend(imgreco.common.find_targets(screenshot, "building/buff_trader3.png"))
                    if len(targets) < 3:
                        targets.extend(imgreco.common.find_targets(screenshot, "building/buff_trader4.png"))
                    if len(targets) < 3:
                        targets.extend(imgreco.common.find_targets(screenshot, "building/buff_trader5.png"))

                for target in targets[:3]:
                    self.tap_rect(target)
                self.screenshot_and_click("building/confirm.png")

            if not drone_used:
                screenshot = self.adb.screenshot()
                tar = imgreco.common.find_target(screenshot, "building/bill_gold.png")
                if tar:
                    self.tap_rect(tar)
                    self.__wait(TINY_WAIT)
                    self.tap_rect(tar)
                    self.__wait(SMALL_WAIT)
                    while self.screenshot_and_click("building/drone_assist.png"):
                        drone_used = True
                        self.__wait(TINY_WAIT)
                        self.screenshot_and_click("building/most_accelerate.png")
                        self.__wait(TINY_WAIT)
                        self.screenshot_and_click("building/confirm_accelerate.png")
                        self.__wait(TINY_WAIT)
                        if self.screenshot_and_click("building/bill_done.png"):
                            self.__wait(MEDIUM_WAIT)
                        else:
                            break
                    self.nav_back(TINY_WAIT)

            self.nav_back(TINY_WAIT)
            i += 1 

        i = 0
        while i < 3:
            originX = self.viewport[0] // 2 + randint(-100, 100)
            originY = self.viewport[1] // 2 + randint(-100, 100)
            self.adb.touch_swipe2((originX, originY), (100.0 * uniform(0.8, 1.2), 0), 255)
            self.__wait(1)

            screenshot = self.adb.screenshot()
            targets = imgreco.common.find_targets(screenshot, "building/power_plant.png")
            if len(targets) <= i:
                break

            self.tap_rect(targets[i])
            self.__wait(TINY_WAIT)

            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/people.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
            elif imgreco.common.find_target(screenshot, "building/people_inverse.png") is None:
                break

            logger.info('进入发电站')

            screenshot = self.adb.screenshot()
            slots = imgreco.common.find_targets(screenshot, "building/add.png")
            if len(slots) > 0:
                self.tap_rect(slots[0])
                self.__wait(TINY_WAIT)

                screenshot = self.adb.screenshot()
                target = imgreco.common.find_target(screenshot, "building/buff_power.png", 0.8)
                if target:
                    self.tap_rect(target)
                self.screenshot_and_click("building/confirm.png")

            self.nav_back(TINY_WAIT)
            i += 1

        i = 0
        while i < 1:
            originX = self.viewport[0] // 2 + randint(-100, 100)
            originY = self.viewport[1] // 2 + randint(-100, 100)
            self.adb.touch_swipe2((originX, originY), (-100.0 * uniform(0.8, 1.2), 0), 255)
            self.__wait(1)

            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/office.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
            else:
                break

            screenshot = self.adb.screenshot()
            tar = imgreco.common.find_target(screenshot, "building/people.png")
            if tar:
                self.tap_rect(tar)
                self.__wait(TINY_WAIT)
            elif imgreco.common.find_target(screenshot, "building/people_inverse.png") is None:
                break

            screenshot = self.adb.screenshot()
            slots = imgreco.common.find_targets(screenshot, "building/add.png")
            if len(slots) > 0:
                self.tap_rect(slots[0])
                self.__wait(TINY_WAIT)

                screenshot = self.adb.screenshot()
                target = imgreco.common.find_target(screenshot, "building/buff_people.png")
                if target:
                    self.tap_rect(target)
                self.screenshot_and_click("building/confirm.png")

            self.nav_back(TINY_WAIT)
            i += 1

    def get_building(self):
        logger.debug("helper.get_building")
        logger.info("清空基建")
        self.back_to_main()
        screenshot = self.adb.screenshot()
        logger.info('进入我的基建')
        self.tap_quadrilateral(imgreco.main.get_back_my_build(screenshot))
        self.__wait(MEDIUM_WAIT + 3)
        self.tap_quadrilateral(imgreco.main.get_my_build_task(screenshot))
        self.__wait(SMALL_WAIT)
        logger.info('收取制造产物')
        self.tap_quadrilateral(imgreco.main.get_my_build_task_clear(screenshot))
        self.__wait(SMALL_WAIT)
        logger.info('清理贸易订单')
        self.tap_quadrilateral(imgreco.main.get_my_sell_task_1(screenshot))
        self.__wait(SMALL_WAIT + 1)
        self.tap_quadrilateral(imgreco.main.get_my_sell_tasklist(screenshot))
        self.__wait(SMALL_WAIT -1 )
        sell_count = 0
        while sell_count <= 6:
            screenshot = self.adb.screenshot()
            self.tap_quadrilateral(imgreco.main.get_my_sell_task_main(screenshot))
            self.__wait(TINY_WAIT)
            sell_count = sell_count + 1
        self.tap_quadrilateral(imgreco.main.get_my_sell_task_2(screenshot))
        self.__wait(SMALL_WAIT - 1)
        sell_count = 0
        while sell_count <= 6:
            screenshot = self.adb.screenshot()
            self.tap_quadrilateral(imgreco.main.get_my_sell_task_main(screenshot))
            self.__wait(TINY_WAIT)
            sell_count = sell_count + 1
        self.back_to_main()
        logger.info("基建领取完毕")

    def log_total_loots(self):
        logger.info('目前已获得：%s', ', '.join('%sx%d' % tup for tup in self.loots.items()))

    def get_inventory_items(self, show_item_name=False):
        import imgreco.inventory
        all_items_map = {}
        if show_item_name:
            import penguin_stats.arkplanner
            all_items_map = penguin_stats.arkplanner.get_all_items_map()

        self.back_to_main()
        logger.info("进入仓库")
        self.tap_rect(imgreco.inventory.get_inventory_rect(self.viewport))

        items_map = {}
        last_screen_items = None
        move = -randint(self.viewport[0] // 4, self.viewport[0] // 3)
        self.__swipe_screen(move)
        screenshot = self.adb.screenshot()
        while True:
            move = -randint(self.viewport[0] // 3.5, self.viewport[0] // 2.5)
            self.__swipe_screen(move)
            screen_items_map = imgreco.inventory.get_all_item_in_screen(screenshot)
            if last_screen_items == screen_items_map.keys():
                logger.info("读取完毕")
                break
            if show_item_name:
                name_map = {all_items_map[k]['name']: screen_items_map[k] for k in screen_items_map.keys()}
                logger.info('name_map: %s' % name_map)
            else:
                logger.info('screen_items_map: %s' % screen_items_map)
            last_screen_items = screen_items_map.keys()
            items_map.update(screen_items_map)
            # break
            screenshot = self.adb.screenshot()
        if show_item_name:
            logger.info('items_map: %s' % {all_items_map[k]['name']: items_map[k] for k in items_map.keys()})
        return items_map

    def __swipe_screen(self, move, rand=100, origin_x=None, origin_y=None):
        origin_x = (origin_x or self.viewport[0] // 2) + randint(-rand, rand)
        origin_y = (origin_y or self.viewport[1] // 2) + randint(-rand, rand)
        self.adb.touch_swipe2((origin_x, origin_y), (move, max(250, move // 2)), randint(600, 900))

    def create_custom_record(self, record_name, roi_size=64, wait_seconds_after_touch=1,
                             description='', back_to_main=True, prefer_mode='match_template', threshold=0.7):
        # FIXME 检查设备是否有 root 权限
        record_dir = os.path.join('custom_record/', record_name)
        if os.path.exists(record_dir):
            c = input('已存在同名的记录, y 覆盖, n 退出: ')
            if c.strip().lower() != 'y':
                return
            import shutil
            shutil.rmtree(record_dir)
        os.mkdir(record_dir)

        if back_to_main:
            self.back_to_main()

        EVENT_LINE_RE = re.compile(r"(\S+): (\S+) (\S+) (\S+)$")
        records = []
        record_data = {
            'screen_width': self.viewport[0],
            'screen_height': self.viewport[1],
            'description': description,
            'prefer_mode': prefer_mode,
            'back_to_main': back_to_main,
            'records': records
        }
        half_roi = roi_size // 2
        logger.info('滑动屏幕以退出录制.')
        logger.info('start recording...')
        sock = self.adb.device_session_factory().shell_stream('getevent')
        f = sock.makefile('rb')
        while True:
            x = 0
            y = 0
            point_list = []
            touch_down = False
            screen = self.adb.screenshot()
            while True:
                line = f.readline().decode('utf-8', 'replace').strip()
                # print(line)
                match = EVENT_LINE_RE.match(line.strip())
                if match is not None:
                    dev, etype, ecode, data = match.groups()
                    if '/dev/input/event5' != dev:
                        continue
                    etype, ecode, data = int(etype, 16), int(ecode, 16), int(data, 16)
                    # print(dev, etype, ecode, data)

                    if (etype, ecode) == (1, 330):
                        touch_down = (data == 1)

                    if touch_down:
                        if 53 == ecode:
                            x = data
                        elif 54 == ecode:
                            y = data
                        elif (etype, ecode, data) == (0, 0, 0):
                            # print(f'point: ({x}, {y})')
                            point_list.append((x, y))
                    elif (etype, ecode, data) == (0, 0, 0):
                        break
            logger.debug(f'point_list: {point_list}')
            if len(point_list) == 1:
                point = point_list[0]
                x1 = max(0, point[0] - half_roi)
                x2 = min(self.viewport[0] - 1, point[0] + half_roi)
                y1 = max(0, point[1] - half_roi)
                y2 = min(self.viewport[1] - 1, point[1] + half_roi)
                roi = screen.crop((x1, y1, x2, y2))
                step = len(records)
                roi.save(os.path.join(record_dir, f'step{step}.png'))
                record = {'point': point, 'img': f'step{step}.png', 'type': 'tap',
                          'wait_seconds_after_touch': wait_seconds_after_touch,
                          'threshold': threshold, 'repeat': 1, 'raise_exception': True}
                logger.info(f'record: {record}')
                records.append(record)
                if wait_seconds_after_touch:
                    logger.info(f'wait {wait_seconds_after_touch}s...')
                    self.__wait(wait_seconds_after_touch)

                logger.info('go ahead...')
            elif len(point_list) > 1:
                # 滑动时跳出循环
                c = input('是否退出录制[Y/n]:')
                if c.strip().lower() != 'n':
                    logger.info('stop recording...')
                    break
                else:
                    # todo 处理屏幕滑动
                    continue
        with open(os.path.join(record_dir, f'record.json'), 'w', encoding='utf-8') as f:
            json.dump(record_data, f, ensure_ascii=False, indent=4, sort_keys=True)

    def replay_custom_record(self, record_name, mode=None, back_to_main=None):
        from PIL import Image
        record_dir = os.path.join('custom_record/', record_name)
        if not os.path.exists(record_dir):
            logger.error(f'未找到相应的记录: {record_name}')
            raise RuntimeError(f'未找到相应的记录: {record_name}')

        with open(os.path.join(record_dir, 'record.json'), 'r', encoding='utf-8') as f:
            record_data = json.load(f)
        logger.info(f'record description: {record_data.get("description")}')
        records = record_data['records']
        if mode is None:
            mode = record_data.get('prefer_mode', 'match_template')
        if mode not in ('match_template', 'point'):
            logger.error(f'不支持的模式: {mode}')
            raise RuntimeError(f'不支持的模式: {mode}')
        if back_to_main is None:
            back_to_main = record_data.get('back_to_main', True)
        if back_to_main:
            self.back_to_main()
        record_height = record_data['screen_height']
        ratio = record_height / self.viewport[1]
        x, y = 0, 0
        for record in records:
            if record['type'] == 'tap':
                repeat = record.get('repeat', 1)
                raise_exception = record.get('raise_exception', True)
                threshold = record.get('threshold', 0.7)
                for _ in range(repeat):
                    if mode == 'match_template':
                        screen = self.adb.screenshot()
                        gray_screen = screen.convert('L')
                        if ratio != 1:
                            gray_screen = gray_screen.resize((int(self.viewport[0] * ratio), record_height))
                        template = Image.open(os.path.join(record_dir, record['img'])).convert('L')
                        (x, y), r = imgreco.imgops.match_template(gray_screen, template)
                        x = x // ratio
                        y = y // ratio
                        logger.info(f'(x, y), r, record: {(x, y), r, record}')
                        if r < threshold:
                            if raise_exception:
                                logger.error('无法识别的图像: ' + record['img'])
                                raise RuntimeError('无法识别的图像: ' + record['img'])
                            break
                    elif mode == 'point':
                        # 这个模式屏幕尺寸宽高比必须与记录中的保持一至
                        assert record_data['screen_width'] == int(self.viewport[0] * ratio)
                        x, y = record['point']
                        x = x // ratio
                        y = y // ratio
                    self.adb.touch_tap((x, y), offsets=(5, 5))
                    if record.get('wait_seconds_after_touch'):
                        self.__wait(record['wait_seconds_after_touch'])
