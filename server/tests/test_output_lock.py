"""出声锁：播报期间必须锁住"耳朵"。

三轮对话串音 / 唤醒自激的根因都在"播报时麦克风没闭上"，这把锁是服务端
兜底。测试只覆盖锁本身的语义，不碰音频。
"""

import time

from app import output_lock


def test_idle_by_default():
    output_lock.clear_output()
    assert output_lock.is_output_active() is False
    assert output_lock.output_remaining() == 0.0


def test_mark_makes_active():
    output_lock.clear_output()
    output_lock.mark_output(2.0)
    assert output_lock.is_output_active() is True
    # 音频时长之外还有回声余量，所以剩余时间必定大于传入时长
    assert output_lock.output_remaining() > 2.0


def test_expired_lock_releases():
    output_lock.clear_output()
    output_lock.mark_output(0.05)
    time.sleep(1.0)  # 0.05s 音频 + 回声余量（0.8s），足够过期
    assert output_lock.is_output_active() is False


def test_clear_unlocks_immediately():
    output_lock.clear_output()
    output_lock.mark_output(30.0)
    assert output_lock.is_output_active() is True
    output_lock.clear_output()  # 用户手动打断播报
    assert output_lock.is_output_active() is False


def test_extend_keeps_the_longer_deadline():
    """分句播报逐句续期：短的那次不能把长的覆盖掉。"""
    output_lock.clear_output()
    output_lock.mark_output(10.0)
    longer = output_lock.output_remaining()
    output_lock.extend_output(0.5)
    assert output_lock.output_remaining() >= longer - 0.1


def test_zero_or_negative_is_ignored():
    output_lock.clear_output()
    output_lock.mark_output(0)
    output_lock.mark_output(-1)
    assert output_lock.is_output_active() is False
