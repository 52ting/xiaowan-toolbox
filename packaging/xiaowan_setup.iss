; =============================================================================
; 小丸工具箱 · Windows 安装程序（Inno Setup 6）
;
; 用法（项目根目录）：
;     ISCC.exe packaging\xiaowan_setup.iss
;
; 产物：
;     installer_out\小丸工具箱-1.2.4-安装程序.exe
;
; 特性：
;   · 默认装到当前用户目录（不需要管理员），高级选项里也能改成装给所有用户
;   · 自动创建开始菜单 + 可选桌面快捷方式
;   · 注册标准卸载入口（控制面板 / 设置 → 应用 里能卸载）
;   · LZMA2 最高压缩，单文件分发
;   · 安装前自动提示关闭正在运行的程序，避免文件占用
; =============================================================================

#define MyAppName "小丸工具箱"
#define MyAppNameEn "XiaoWan Toolbox"
#define MyAppVersion "1.2.4"
#define MyAppPublisher "52ting"
#define MyAppURL "https://github.com/52ting/xiaowan-toolbox"
#define MyAppExeName "小丸工具箱.exe"

[Setup]
; AppId 一旦发布就不要改，否则会被当成另一个程序（升级时无法覆盖安装）
AppId={{7B2C4A16-9E3D-4F58-A1C7-3D8E5B9F2A44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

; 默认装到当前用户目录 → 全程无需管理员权限
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
; 只在命令行显式给 /ALLUSERS 时才走"装给所有用户"，
; 不给的话不弹「选择安装模式」页——普通用户少回答一个问题
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
AllowNoIcons=yes
LicenseFile=..\LICENSE
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

; 只支持 64 位 Windows（程序本身是 64 位的）
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

OutputDir=..\installer_out
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-安装程序
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 跟随系统语言自动选（中文系统直接给中文），不额外弹「选择安装语言」页
ShowLanguageDialog=no
SetupLogging=yes

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 整个免安装目录原样搬进安装目录
Source: "..\dist\{#MyAppName}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 让 Win+R 输入「小丸工具箱」就能启动
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; \
    ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName}"; \
    Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; \
    ValueType: string; ValueName: "Path"; ValueData: "{app}"; \
    Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 清掉运行时可能在安装目录里留下的崩溃日志（用户配置在 %APPDATA% 里，保留）
Type: files; Name: "{app}\crash.log"
