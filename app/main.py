"""Chinese desktop control panel. Telemetry and speech remain separate services."""
import argparse,json,sys,time,hashlib,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Qt,QTimer,QProcess
from PySide6.QtGui import QColor,QIcon,QPainter,QPixmap,QFont,QFontDatabase
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,
    QPushButton,QListWidget,QStackedWidget,QFrame,QSlider,QTableWidget,QTableWidgetItem,QHeaderView,
    QPlainTextEdit,QSystemTrayIcon,QMenu,QMessageBox,QComboBox,QScrollArea,QLayout,QSizeGrip)
from backend import Backend,atomic_json
if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'work/race-voices'))
from app_lifecycle import claim, release
from PySide6.QtNetwork import QLocalServer,QLocalSocket

STYLE='''
QMainWindow{background:#edf6ff;}
QWidget{background:transparent;color:#244765;font-family:"Microsoft YaHei";font-size:14px;}
QFrame#card{background:rgba(255,255,255,224);border:1px solid #c6ddf1;border-radius:14px;}
QFrame#card[tone="blue"]{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #eaf4ff,stop:1 #f6fbff);border:1px solid #9dc9f1;}
QFrame#card[tone="lavender"]{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #f1edff,stop:1 #f9f8ff);border:1px solid #d5cbf2;}
QFrame#card[tone="mint"]{background:rgba(244,253,253,230);border:1px solid #b5dfe3;}
QFrame#sidebar{background:rgba(246,251,255,215);border-right:1px solid #c2d9ed;}
QFrame#chrome{background:rgba(242,249,255,215);border-bottom:1px solid #c5ddf0;}
QPushButton#windowControl{background:transparent;border:0;border-radius:6px;padding:0;font-size:17px;}
QPushButton#windowControl:hover{background:#d9ecff;}
QPushButton#closeControl{background:transparent;border:0;border-radius:6px;padding:0;font-size:19px;}
QPushButton#closeControl:hover{background:#ed7182;color:white;}
QScrollArea{border:0;background:transparent;}
QScrollBar:vertical{background:rgba(235,246,255,145);width:9px;margin:3px;}
QScrollBar::handle:vertical{background:#aecde8;min-height:35px;border-radius:3px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}
QSizeGrip{background:transparent;width:14px;height:14px;}
QLabel{background:transparent;border:none;}
QLabel#title{font-size:30px;font-weight:700;color:#204c75;}
QLabel#section{font-size:20px;font-weight:650;color:#2b5275;}
QLabel#muted{color:#5d7b98;}
QLabel#value{font-size:40px;font-weight:700;color:#378bd5;}
QPushButton{background:#eef6ff;border:1px solid #bcd6ee;border-radius:9px;padding:11px 16px;color:#305b80;}
QPushButton:hover{background:#dceeff;border:1px solid #89bcec;}
QPushButton:pressed{background:#c7e3ff;}
QPushButton:disabled{color:#98aec1;background:#f3f7fb;border:1px solid #d9e5ef;}
QPushButton#primary{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #368cf3,stop:1 #2469e0);color:white;font-weight:bold;border:1px solid #2b76e6;}
QPushButton#primary:hover{background:#347dec;}
QListWidget{background:transparent;border:none;outline:0;font-size:17px;}
QListWidget::item{padding:18px 14px;margin:3px 10px;border-radius:7px;color:#446b90;}
QListWidget::item:hover{background:rgba(218,236,255,180);}
QListWidget::item:selected{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3c97fa,stop:1 #2874ec);color:white;border-left:3px solid #85dfff;}
QPlainTextEdit{background:rgba(255,255,255,235);color:#315577;border:1px solid #c2d9ed;border-radius:8px;padding:10px;font-size:13px;}
QLineEdit{background:#ffffff;border:1px solid #bad3eb;border-radius:6px;padding:7px;}
QSlider::groove:horizontal{height:7px;background:#dcebf7;border-radius:3px;}
QSlider::sub-page:horizontal{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8acfff,stop:1 #408bf0);border-radius:3px;}
QSlider::handle:horizontal{background:white;border:2px solid #63a8ef;width:17px;margin:-6px 0;border-radius:9px;}
QComboBox{background:#f5faff;padding:9px;border:1px solid #b7d3ed;border-radius:6px;color:#315577;}
QComboBox QAbstractItemView{background:white;color:#315577;selection-background-color:#dcecff;selection-color:#245591;}
QMenu{background:#f8fcff;color:#315577;border:1px solid #bdd5ec;}
QMenu::item:selected{background:#dcecff;}
QToolTip{background:#f5fbff;color:#315577;border:1px solid #b8d4ec;padding:5px;}
QDialog{background:#edf7ff;}
'''


def label(text,style=None):
    w=QLabel(text)
    if style:w.setObjectName(style)
    return w

def button(text,fn,primary=False):
    b=QPushButton(text);b.clicked.connect(fn)
    if primary:b.setObjectName('primary')
    return b

def panel(title=None):
    frame=QFrame();frame.setObjectName('card');layout=QVBoxLayout(frame);layout.setContentsMargins(20,18,20,18);layout.setSpacing(14)
    if title:layout.addWidget(label(title,'section'))
    return frame,layout

def icon(project):
    return QIcon(str(project/'app/assets/app-icon.ico'))

class ScenicBackdrop(QWidget):
    def __init__(self,path):
        super().__init__();self.scene=QPixmap(str(path));self.cached_scene=None;self.cached_size=None
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.SmoothPixmapTransform);painter.fillRect(self.rect(),QColor('#edf6ff'))
        if not self.scene.isNull():
            if self.cached_size!=self.size():
                self.cached_scene=self.scene.scaled(self.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation);self.cached_size=self.size()
            scaled=self.cached_scene
            painter.drawPixmap((self.width()-scaled.width())//2,0,scaled)
            painter.fillRect(self.rect(),QColor(246,251,255,48))
        painter.end()

class TitleBar(QFrame):
    def __init__(self,window):
        super().__init__();self.window=window;self.drag_offset=None;self.setObjectName('chrome');self.setFixedHeight(46)
        row=QHBoxLayout(self);row.setContentsMargins(18,0,8,0);row.setSpacing(5)
        row.addWidget(label('iracing中文播报助手'));row.addStretch();tag=label('本地语音 · 双角色','muted');row.addWidget(tag);row.addSpacing(20)
        for text,fn,name,title in [('—',window.showMinimized,'windowControl','最小化'),('□',self.toggle_max,'windowControl','最大化／还原'),('×',window.close,'closeControl','关闭到托盘')]:
            control=QPushButton(text);control.setObjectName(name);control.setToolTip(title);control.setAccessibleName(title);control.setFixedSize(39,32);control.clicked.connect(fn);row.addWidget(control)
    def toggle_max(self):self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.drag_offset=event.globalPosition().toPoint()-self.window.frameGeometry().topLeft();event.accept();return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.drag_offset is not None and event.buttons()&Qt.LeftButton and not self.window.isMaximized():
            self.window.move(event.globalPosition().toPoint()-self.drag_offset);event.accept();return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        self.drag_offset=None;super().mouseReleaseEvent(event)
    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.LeftButton:self.toggle_max()

class Window(QMainWindow):
    def __init__(self,backend):
        super().__init__();self.backend=backend;self.setWindowTitle('iracing中文播报助手');self.setWindowIcon(icon(backend.project));self.resize(1340,900);self.setMinimumSize(1080,760)
        self.setWindowFlag(Qt.FramelessWindowHint,True)
        self.state={};self.job=None;self.job_kind='';self.pool=ThreadPoolExecutor(max_workers=1);self.future=None;self.closing=False
        central=ScenicBackdrop(self.backend.project/'app/assets/sky-palace.png');self.setCentralWidget(central);shell=QVBoxLayout(central);shell.setContentsMargins(0,0,0,0);shell.setSpacing(0);self.title_bar=TitleBar(self);shell.addWidget(self.title_bar)
        body=QWidget();shell.addWidget(body,1);outer=QHBoxLayout(body);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(205);sl=QVBoxLayout(sidebar);sl.setContentsMargins(0,25,0,18)
        mark=label('');mark.setAccessibleName('中文播报助手标志');mark.setPixmap(QPixmap(str(backend.project/'app/assets/app-logo.png')).scaled(76,76,Qt.KeepAspectRatio,Qt.SmoothTransformation));mark.setAlignment(Qt.AlignCenter);sl.addWidget(mark);sl.addSpacing(22)
        self.nav=QListWidget();self.nav.addItems(['运行概览','赛事策略\n测试中','音色管理','复盘','诊断日志']);sl.addWidget(self.nav)
        self.nav.item(1).setToolTip('赛事策略 · 测试中：默认关闭，需手动开启。')
        from update_client import current_version
        sl.addWidget(label('   v'+current_version(backend.project)+' · 中文播报助手','muted'));sl.addWidget(button('检查更新',self.open_updates));outer.addWidget(sidebar)
        self.pages=QStackedWidget();outer.addWidget(self.pages,1);self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.build_home();self.build_strategy();self.build_voices();self.build_reviews();self.build_diagnostics();self.nav.setCurrentRow(0)
        bottom=QHBoxLayout();bottom.setContentsMargins(0,0,4,3);bottom.addStretch();bottom.addWidget(QSizeGrip(self));shell.addLayout(bottom)
        self.tray=QSystemTrayIcon(icon(backend.project),self);self.tray.setToolTip('iracing中文播报助手');menu=QMenu()
        menu.addAction('显示窗口',self.show_window);menu.addAction('检查更新',self.open_updates);menu.addAction('停止播报',lambda:self.run_ps('stop'));menu.addSeparator();menu.addAction('退出应用并关闭组件',self.quit_ui)
        self.tray.setContextMenu(menu);self.tray.activated.connect(lambda reason:self.show_window() if reason==QSystemTrayIcon.DoubleClick else None);self.tray.show()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(400);self.tick()

    def page(self,title,subtitle):
        page=QWidget();v=QVBoxLayout(page);v.setContentsMargins(26,22,26,18);v.setSpacing(14);v.setSizeConstraint(QLayout.SetMinimumSize)
        v.addWidget(label(title,'title'))
        if subtitle:v.addWidget(label(subtitle,'muted'))
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame);scroll.setWidget(page)
        self.pages.addWidget(scroll);return v

    def build_home(self):
        v=self.page('运行概览','');v.setSpacing(10);v.setContentsMargins(22,16,22,14)
        top=QHBoxLayout();self.running=label('● 检查连接中');top.addWidget(self.running);top.addStretch();top.addWidget(button('测试声音',lambda:self.test_voice('clorinde')));self.start_button=button('启动播报',self.toggle_service,True);top.addWidget(self.start_button);v.addLayout(top)
        row=QHBoxLayout();self.cards={}
        for name in ['iRacing','语音引擎']:
            card,layout=panel();layout.setContentsMargins(16,12,16,12);layout.setSpacing(6);card.setProperty('tone','blue' if name=='iRacing' else 'lavender');line=QHBoxLayout();line.addWidget(label(name,'muted'));line.addStretch();value=label('正在检查','section');line.addWidget(value);layout.addLayout(line);detail=label('—','muted');detail.setWordWrap(True);layout.addWidget(detail);row.addWidget(card,1);self.cards[name]=(value,detail)
        v.addLayout(row)
        v.addWidget(button('Simulator Controller 安装引导',self.open_simulator_guide))
        card,c=panel();card.setProperty('tone','blue');c.setContentsMargins(16,12,16,12);c.setSpacing(6)
        row=QHBoxLayout();copy=QVBoxLayout();copy.setSpacing(6);copy.addWidget(label('固定语音缓存','section'))
        note=label('赛前生成常用语音，减少播报等待。','muted');copy.addWidget(note)
        self.cache_status=label('启动播报后、进入赛道前预热。','muted');self.cache_status.setWordWrap(True);copy.addWidget(self.cache_status)
        row.addLayout(copy,1);self.cache_button=button('预热固定缓存',self.warm_fixed_cache,True);self.cache_button.setMinimumWidth(170);self.cache_button.setToolTip('只生成语音，不播放声音。首次预热会占用 CPU；已有缓存可复用。');row.addWidget(self.cache_button);c.addLayout(row);v.addWidget(card)
        modules=QHBoxLayout();modules.setSpacing(10);modules.addWidget(self.build_settings(),1);modules.addWidget(self.build_controller(),1);v.addLayout(modules)
        v.addStretch();self.footer=label('语音在本机合成 · 按键触发一次状态摘要','muted');v.addWidget(self.footer)

    def build_settings(self):
        card,c=panel('播报设置');c.setContentsMargins(16,14,16,14);c.setSpacing(10)
        line=QHBoxLayout();line.addWidget(label('播报音量','muted'));line.addStretch();self.volume_value=label('100%','section');line.addWidget(self.volume_value);c.addLayout(line)
        self.volume=QSlider(Qt.Horizontal);self.volume.setRange(0,100)
        try:percent=json.loads((self.backend.root/'playback_settings.json').read_text(encoding='utf-8')).get('volume_percent',100)
        except (OSError,ValueError):percent=100
        self.volume.setValue(int(percent));self.volume_value.setText(str(percent)+'%');self.volume.valueChanged.connect(lambda value:self.volume_value.setText(str(value)+'%'));self.volume.sliderReleased.connect(self.save_volume);c.addWidget(self.volume)
        self.volume.setToolTip('调整助手播报音量；系统音量由 Windows 控制。')
        self.engineer_label=label('工程师 · 克洛琳德','section');c.addWidget(self.engineer_label);note=label('车辆、油量、轮胎、维修与按键摘要','muted');note.setWordWrap(True);c.addWidget(note)
        self.observer_label=label('观察员／策略师 · 芙宁娜','section');c.addWidget(self.observer_label);note=label('圈速、邻车、旗号、天气与赛事策略','muted');note.setWordWrap(True);c.addWidget(note)
        row=QHBoxLayout();row.addWidget(button('试听工程师',lambda:self.test_voice('clorinde')));row.addWidget(button('试听观察员',lambda:self.test_voice('furina')));c.addLayout(row);c.addStretch();return card

    def build_controller(self):
        card,c=panel('实时播报按键');c.setContentsMargins(16,14,16,14);c.setSpacing(10);card.setProperty('tone','mint');self.binding_title=label('读取当前绑定…','section');c.addWidget(self.binding_title);self.binding_detail=label('');self.binding_detail.setWordWrap(True);c.addWidget(self.binding_detail)
        note=label('按一次，播报车况、油量和进站建议。','muted');note.setWordWrap(True);c.addWidget(note)
        note=label('点击绑定，松开所有按键，再按下目标键。','muted');note.setWordWrap(True);c.addWidget(note)
        row=QHBoxLayout();self.bind_button=button('绑定播报按键',self.begin_bind,True);row.addWidget(self.bind_button);self.cancel_button=button('取消绑定',self.cancel_bind);self.cancel_button.setEnabled(False);row.addWidget(self.cancel_button);row.addStretch();c.addLayout(row)
        self.binding_result=label('长按不连播；换设备后重新绑定。');self.binding_result.setWordWrap(True);c.addWidget(self.binding_result)
        self.bind_button.setToolTip('等待 30 秒；取消或超时保留原绑定。支持控制器前 32 个按钮。转向和踏板请在 iRacing 内校准。');c.addStretch();return card

    def build_strategy(self):
        from strategy_page import StrategyPage
        self.strategy_page=StrategyPage(self.backend.project);self.pages.addWidget(self.strategy_page)

    def build_voices(self):
        from voices_ui import VoiceLibraryWidget
        v=self.page('音色管理','两个角色分别选择音色，默认音色始终保留')
        card,c=panel('角色音色与音色库');self.voice_library=VoiceLibraryWidget(self.backend,self.reload_voices);c.addWidget(self.voice_library);v.addWidget(card)
        card,c=panel('本地语音引擎');self.engine_info=label('正在读取服务状态…');c.addWidget(self.engine_info);c.addWidget(label('双角色共用一个语音引擎，按需切换模型。','muted'));row=QHBoxLayout();row.addWidget(button('试听观察员／策略师',lambda:self.test_voice('furina')));row.addWidget(button('试听工程师',lambda:self.test_voice('clorinde')));c.addLayout(row);v.addWidget(card)
        card,c=panel('安装组件');c.addWidget(label('完整安装版包含本地引擎、Python／PyTorch、基础模型和两套默认音色。','muted'));c.addWidget(label('首次合成需要加载模型；固定提示可提前预热。','muted'));v.addWidget(card);v.addStretch()

    def build_diagnostics(self):
        v=self.page('诊断日志','运行目录、服务健康状态与最近错误')
        self.diag=QPlainTextEdit();self.diag.setReadOnly(True);v.addWidget(self.diag,1)
        row=QHBoxLayout();row.addWidget(button('刷新诊断',self.refresh_diagnostics));row.addWidget(button('打开后台目录',self.open_backend));row.addWidget(button('打开安装说明',self.open_docs));v.addLayout(row)

    def build_reviews(self):
        from reviews_page import ReviewsPage
        self.reviews_page=ReviewsPage(self.backend.project);self.pages.addWidget(self.reviews_page)

    def start_process(self,program,args,kind):
        if self.job and self.job.state()!=QProcess.NotRunning:
            self.notify('已有任务运行中，请等待完成。');return False
        self.job=QProcess(self);self.job.setProgram(str(program));self.job.setArguments([str(x) for x in args]);self.job.setWorkingDirectory(str(self.backend.root));self.job_kind=kind;self.job.setProcessChannelMode(QProcess.MergedChannels)
        self.job.finished.connect(self.job_done);self.job.errorOccurred.connect(lambda error:self.notify('任务启动失败，请检查运行环境。'));self.job.start();return True

    def run_ps(self,name,extra=None):
        if getattr(self,'closing',False):return
        path=self.backend.script(name)
        if not path.exists():self.notify('未找到安装工具：'+str(path));return
        self.start_process('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',str(path),*(extra or [])],name)

    def run_python(self,script,args=None,kind='tool'):
        if not self.backend.python.exists():self.notify('后台 Python 不存在，请先安装。');return False
        return self.start_process(self.backend.python,['-X','utf8',str(self.backend.root/script),*(args or [])],kind)

    def toggle_service(self):self.run_ps('stop' if self.state.get('health',{}).get('status')=='ready' else 'start')
    def warm_fixed_cache(self):
        self.cache_status.setStyleSheet('color:#1683ef;font-weight:700;')
        if self.state.get('health',{}).get('status')!='ready':
            self.cache_status.setText('请先点击“启动播报”，等待语音引擎连接后再预热。');return
        if self.state.get('game'):
            self.cache_status.setText('请先退出驾驶会话，再进行预热。');return
        if self.run_python('warm_cache.py',kind='cache'):
            self.cache_button.setEnabled(False);self.cache_status.setText('正在生成固定语音缓存；完成前请勿进入驾驶会话。')
    def reload_voices(self):
        if self.job and self.job.state()!=QProcess.NotRunning:
            self.notify('音色已保存，请在当前任务结束后停止并重新启动播报。');return
        if self.state.get('health',{}).get('status')=='ready':self.restart_after_stop=True;self.run_ps('stop')
        else:self.notify('角色音色已保存，下次启动播报生效。')
    def test_voice(self,voice):self.run_python('desktop_speech_test.py',[self.backend.url,voice],kind='speech')
    def save_volume(self):
        atomic_json(self.backend.root/'playback_settings.json',{'volume_percent':self.volume.value()});self.notify('音量已保存，新播报生效。')
    def begin_bind(self):
        if self.run_python('button_binding.py',kind='binding'):
            self.bind_button.setEnabled(False);self.cancel_button.setEnabled(True);self.binding_started=time.monotonic();self.binding_result.setText('等待按键：请松开所有按钮，再按下播报按钮。')
    def cancel_bind(self):(self.backend.root/'button_binding.cancel').write_text('cancel',encoding='utf-8')
    def job_done(self,code,status):
        text=bytes(self.job.readAllStandardOutput()).decode('utf-8',errors='replace').strip()
        if self.job_kind=='cache':
            self.cache_button.setEnabled(True)
            self.cache_status.setText('预热已暂停，请退出驾驶会话后再次运行。' if 'Paused:' in text else ('固定缓存预热完成，可以开始比赛。' if code==0 and status==QProcess.NormalExit else '预热未完成，请查看诊断日志后重试。'))
        if self.job_kind=='binding':
            self.binding_result.setText(text or '绑定任务已结束。');self.bind_button.setEnabled(True);self.cancel_button.setEnabled(False)
        self.diag.appendPlainText(f'\n任务 {self.job_kind}，退出码 {code}\n'+text[-5000:]);self.footer.setText('任务完成' if code==0 else '任务失败，请查看诊断日志')
        self.job=None
        if not self.closing and self.job_kind=='stop' and getattr(self,'restart_after_stop',False):
            self.restart_after_stop=False
            if code==0:self.run_ps('start')
    def notify(self,text):self.footer.setText(text)
    def open_backend(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.backend.root)))
    def open_docs(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.backend.project/'docs/INSTALL.md')))
    def open_simulator_guide(self):
        from simulator_guide import SimulatorGuide
        if not getattr(self,'simulator_guide',None):self.simulator_guide=SimulatorGuide(self.backend,self)
        self.simulator_guide.refresh();self.simulator_guide.show();self.simulator_guide.raise_();self.simulator_guide.activateWindow()
    def refresh_diagnostics(self):
        text='后台目录：'+str(self.backend.root)+'\n语音地址：'+self.backend.url+'\n\n'+json.dumps(self.state.get('health',{}),ensure_ascii=False,indent=2)
        for name in ['telemetry-error.log','server-error.log']:
            path=self.backend.root/name
            if path.exists():
                with path.open('rb') as f:f.seek(max(0,path.stat().st_size-5000));tail=f.read().decode('utf-8',errors='replace')
                text+='\n\n'+name+'（末尾）\n'+tail
        self.diag.setPlainText(text)

    def tick(self):
        if self.future and self.future.done():
            try:self.update_state(self.future.result())
            except Exception as e:self.notify('状态读取失败：'+type(e).__name__)
            self.future=None
        if self.future is None:self.future=self.pool.submit(self.backend.poll)
        if self.job and self.job_kind=='binding' and self.job.state()!=QProcess.NotRunning:
            remain=max(0,30-int(time.monotonic()-self.binding_started));self.binding_result.setText(f'等待按键……剩余 {remain} 秒。先松开，再按下一个按钮。')

    def update_state(self,s):
        if self.strategy_page.enabled.isChecked():self.strategy_page.update_connection(s)
        entries=self.voice_library.catalog.entries();roles=self.voice_library.catalog.roles();engineer_name=entries[roles['engineer']]['name'];observer_name=entries[roles['observer']]['name']
        self.engineer_label.setText('工程师 · '+engineer_name);self.observer_label.setText('观察员／策略师 · '+observer_name)
        self.state=s;ready=s['health'].get('status')=='ready';self.running.setText('● 正在运行' if ready else '● 播报服务未启动');self.running.setStyleSheet('color:'+('#167347' if ready else '#946211'));self.start_button.setText('停止播报' if ready else '启动播报')
        self.cards['iRacing'][0].setText('已连接' if s['game'] else '等待游戏');self.cards['iRacing'][1].setText(s['car'])
        self.cards['语音引擎'][0].setText('运行正常' if ready else '未连接');self.cards['语音引擎'][1].setText('本地双音色 · CPU' if ready else '点击启动播报')
        b=s.get('binding',{});cfg=s.get('binding_config',{});fresh=time.time()-b.get('time',0)<6;connected=b.get('connected') and fresh
        bound=type(cfg.get('device')) is int and type(cfg.get('button')) is int and 1<=cfg['device']<=16 and 1<=cfg['button']<=32
        detail=f"设备 {cfg.get('device','—')} · 按钮 {cfg.get('button','—')}";self.binding_title.setText('当前绑定：'+detail if bound else '尚未绑定播报键');self.binding_detail.setText(('按键接口在线' if connected else '暂无在线按键读数，请连接控制器。') if bound else '点击下方按钮设置。')
        engine=s['health'].get('engine','gpt-sovits' if ready else '未连接');self.engine_info.setText('当前引擎：'+engine+'；可用音色：'+('、'.join(s['health'].get('voices',[])) or '暂无数据'))

    def open_updates(self):
        from update_dialog import UpdateDialog
        if not getattr(self,'update_dialog',None) or not self.update_dialog.isVisible():
            self.update_dialog=UpdateDialog(self.backend,self,can_apply=lambda:not self.state.get('game') and (not self.job or self.job.state()==QProcess.NotRunning),on_launch=self.quit_ui)
        self.update_dialog.show();self.update_dialog.raise_();self.update_dialog.activateWindow()

    def show_window(self):self.showNormal();self.raise_();self.activateWindow()
    def closeEvent(self,event):
        if not self.closing:event.ignore();self.hide();self.tray.showMessage('iracing中文播报助手','窗口已收起到托盘，后台继续运行。')
        else:event.accept()
    def quit_ui(self):
        if self.closing:return
        if self.job and self.job_kind=='binding':self.cancel_bind()
        self.closing=True;self.timer.stop();self.pool.shutdown(wait=False,cancel_futures=True)
        owner=getattr(self,'service_owner',None)
        if not owner:self.finish_quit();return
        release(self.backend.root,owner)
        # Stop independently of an in-flight cache/start/binding task.
        self.setEnabled(False);self.footer.setText('正在关闭语音和 Simulator Controller 组件……')
        self.shutdown_job=QProcess(self)
        self.shutdown_job.finished.connect(lambda *_:self.finish_quit())
        self.shutdown_job.errorOccurred.connect(lambda *_:self.finish_quit())
        self.shutdown_job.start(str(self.backend.python),['-X','utf8',str(self.backend.root/'stop_services.py')])
        QTimer.singleShot(35000,self.finish_quit)

    def finish_quit(self):
        self.tray.hide();QApplication.quit()

def main():
    parser=argparse.ArgumentParser();default=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    parser.add_argument('--project-dir',default=str(default));parser.add_argument('--backend-dir');parser.add_argument('--smoke-test',action='store_true');parser.add_argument('--install-simulator',action='store_true');parser.add_argument('--quit',action='store_true');args=parser.parse_args()
    app=QApplication(sys.argv[:1])
    # Load installed system fonts; fonts are not redistributed with this app.
    if sys.platform=='win32':
        for name in ['msyh.ttc','msyhbd.ttc','segoeui.ttf']:
            font=Path(os.environ.get('SystemRoot','C:/Windows'))/'Fonts'/name
            if font.exists():QFontDatabase.addApplicationFont(str(font))
    app.setStyle('Fusion');app.setStyleSheet(STYLE);app.setApplicationName('iracing中文播报助手');app.setQuitOnLastWindowClosed(False)
    backend=Backend(args.project_dir,args.backend_dir)
    app.setWindowIcon(icon(backend.project))
    key='iracing-assistant-'+hashlib.sha256(str(backend.root).encode()).hexdigest()[:16]
    client=QLocalSocket();client.connectToServer(key)
    if client.waitForConnected(300):client.write(b'quit\n' if args.quit else (b'install\n' if args.install_simulator else b'show\n'));client.flush();client.waitForBytesWritten(300);return 0
    if args.quit:return 0
    server=QLocalServer();QLocalServer.removeServer(key)
    if not server.listen(key):return 1
    window=Window(backend)
    if not args.smoke_test:
        window.service_owner=claim(backend.root)
        app.aboutToQuit.connect(lambda:release(backend.root,window.service_owner))
        QTimer.singleShot(0,lambda:window.run_ps('start'))
    def install():
        window.show_window();window.open_simulator_guide();window.simulator_guide.install_and_connect()
    def receive():
        connection=server.nextPendingConnection()
        def dispatch():
            if not connection.canReadLine():return
            command=bytes(connection.readLine()).strip()
            if command==b'quit':window.quit_ui()
            else:
                window.show_window()
                if command==b'install':install()
            connection.disconnectFromServer();connection.deleteLater()
        connection.readyRead.connect(dispatch)
        if connection.bytesAvailable():dispatch()
    server.newConnection.connect(receive);window.show()
    if args.install_simulator:QTimer.singleShot(0,install)
    if args.smoke_test:QTimer.singleShot(5000,window.quit_ui)
    return app.exec()

if __name__=='__main__':sys.exit(main())
