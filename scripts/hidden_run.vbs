' Starts a PowerShell script with NO window at all (window style 0). A scheduled task that starts powershell.exe directly flashes a console even with -WindowStyle Hidden;
' wscript.exe has no console, so nothing appears. Usage: wscript.exe hidden_run.vbs "<full path of the .ps1>"
Set sh = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & WScript.Arguments(0) & """"
sh.Run cmd, 0, False
