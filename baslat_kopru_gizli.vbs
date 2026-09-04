Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "C:\Users\AliYokus\Desktop\github_project"

' SQL Bridge'i gizli arka planda baslat (port 5001)
objShell.Run """C:\Users\AliYokus\AppData\Local\Programs\Python\Python314\python.exe"" sql_bridge.py", 0, False

' 4 saniye bekle - koprunun hazir olmasi icin
WScript.Sleep 4000

' ngrok ile 5001 portunu internete ac
objShell.Run "ngrok http 5001", 0, False
