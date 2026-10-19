"""Non-blocking release check/download UI; a separate installer replaces files."""
import os,queue,subprocess,webbrowser
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QProgressBar,QPlainTextEdit
import update_client as client

class UpdateDialog(QDialog):
    def __init__(self,backend,parent=None,can_apply=lambda:True,on_launch=lambda:None,auto=True):
        super().__init__(parent);self.backend=backend;self.can_apply=can_apply;self.on_launch=on_launch
        self.pool=ThreadPoolExecutor(max_workers=1);self.future=None;self.kind='';self.release=None;self.percent=queue.SimpleQueue()
        self.setWindowTitle('检查更新');self.resize(650,480)
        layout=QVBoxLayout(self);layout.addWidget(QLabel('当前版本：'+client.current_version(backend.project)))
        self.status=QLabel('点击检查，从 GitHub 查找适用于当前版本的最新补丁。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.notes=QPlainTextEdit();self.notes.setReadOnly(True);layout.addWidget(self.notes)
        self.progress=QProgressBar();self.progress.setValue(0);layout.addWidget(self.progress)
        row=QHBoxLayout();self.check_button=QPushButton('检查更新');self.check_button.clicked.connect(self.check);row.addWidget(self.check_button)
        self.install_button=QPushButton('下载补丁并更新');self.install_button.setEnabled(False);self.install_button.clicked.connect(self.install);row.addWidget(self.install_button)
        page=QPushButton('打开发布页');page.clicked.connect(lambda:webbrowser.open(client.PAGE));row.addWidget(page);layout.addLayout(row)
        note=QLabel('只下载匹配版本的补丁，保留模型、设置和缓存。更新前请退出驾驶会话；补丁校验后关闭应用，备份并更新，再重新打开。');note.setWordWrap(True);layout.addWidget(note)
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(100)
        if auto:QTimer.singleShot(0,self.check)

    def check(self):
        if self.future:return
        self.status.setText('正在查询 GitHub 正式发布的更新补丁……');self.check_button.setEnabled(False);self.install_button.setEnabled(False)
        self.kind='check';self.future=self.pool.submit(client.check,self.backend.project)

    def install(self):
        if self.future or not self.release or not self.release.get('asset'):return
        if not self.can_apply():self.status.setText('请先退出驾驶会话，并等待当前任务完成。');return
        self.status.setText('正在下载更新补丁……');self.progress.setValue(0);self.check_button.setEnabled(False);self.install_button.setEnabled(False)
        self.kind='download';self.future=self.pool.submit(client.download,self.release['asset'],self.backend.project/'.updates/downloads',self.percent.put)

    def tick(self):
        while not self.percent.empty():self.progress.setValue(self.percent.get())
        if not self.future or not self.future.done():return
        future=self.future;self.future=None;self.check_button.setEnabled(True)
        try:
            result=future.result()
            if self.kind=='check':
                self.release=result;self.notes.setPlainText(result['notes'])
                if result['state']=='current':self.status.setText('已安装版本不低于最新正式版 '+result['latest']+'。')
                elif result['asset']:
                    self.status.setText('可更新至 '+result['latest']+'，补丁约 '+str(round(result['asset']['size']/1024**2,1))+' MB。');self.install_button.setEnabled(True)
                else:self.status.setText('发现 '+result['latest']+'，暂无匹配当前版本的补丁；不下载完整安装包，保留当前版本。')
            else:
                if not self.can_apply():self.status.setText('文件已下载。请退出驾驶会话并等待当前任务完成后再更新。');self.install_button.setEnabled(True);return
                subprocess.Popen([str(result),'/DIR='+str(self.backend.project),'/WAITPID='+str(os.getpid())],cwd=result.parent)
                self.status.setText('更新器已启动，正在退出当前界面。');self.on_launch();self.close()
        except Exception as error:
            self.status.setText('更新未完成：'+str(error)+'。当前播报不受影响，可稍后重试。')
            self.install_button.setEnabled(bool(self.release and self.release.get('asset')))

    def closeEvent(self,event):
        if self.future:event.ignore();self.status.setText('检查或下载仍在进行，请稍候。');return
        self.timer.stop();self.pool.shutdown(wait=False,cancel_futures=True);event.accept()
