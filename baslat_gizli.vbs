Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "C:\Users\AliYokus\Desktop\github_project"

' Flask'i gizli arka planda baslat
objShell.Run """C:\Users\AliYokus\AppData\Local\Programs\Python\Python314\python.exe"" app.py", 0, False

' 3 saniye bekle
WScript.Sleep 3000

' ngrok tunel ac
objShell.Run "ngrok http 5000", 0, False
