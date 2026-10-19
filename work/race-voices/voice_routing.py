"""Enforce the two spoken roles at the shared synthesis boundary."""
def route_voice(text,requested):
    if text in ('本次复盘已经保存。','复盘文本已保存，请进入软件查看。') or text.startswith('本次计时赛记录'):return 'furina'
    if text.startswith('我是芙宁娜') or text.startswith('本圈'):return 'furina'
    if text.startswith('我是克洛琳德'):return 'clorinde'
    if any(word in text for word in ('通讯按钮','车辆无告警','当前车辆状态','剩余油量','当前进站服务',
             '赛车已经准备就绪','本次比赛结束，期待下一次合作','正在整理具体数据','当前赛道是')):return 'clorinde'
    if any(word in text for word in ('黄旗','蓝旗','黑旗','红旗','绿旗','白旗','方格旗','橙圆旗','油旗')):return 'furina'
    if any(word in text for word in ('胎压','胎温','胎纹','轮胎','维修','油压','水温','动力异常','油量','燃油','碰撞事故记录','发动机','车辆状态','告警','制动异常','方向异常')):return 'clorinde'
    if any(word in text for word in ('气温','赛道温度','天气','降雨','下雨','赛道干燥','赛道湿滑','赛道积水',
              '抓地','橡胶','左侧','右侧','两侧','前一名','后一名','前车','后车','号车','前方发生事故',
              '后方发生事故','领跑车','策略','建议进站','建议本圈进站')):return 'furina'
    return requested
