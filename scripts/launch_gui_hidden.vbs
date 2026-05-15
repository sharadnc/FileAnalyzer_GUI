' Launch File Analyzer GUI without a console window.
' Args: (1) Python executable full path, OR the literal PY to use py -3w
'       (2) Working directory (repo root)
'       (3) Path to src\main.py

Option Explicit

If WScript.Arguments.Count < 3 Then WScript.Quit 1

Dim exeOrTag, rootDir, mainPy, shell, cmdLine

exeOrTag = WScript.Arguments(0)
rootDir = WScript.Arguments(1)
mainPy = WScript.Arguments(2)

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = rootDir

If UCase(Trim(exeOrTag)) = "PY" Then
  cmdLine = "py -3w """ & mainPy & """"
Else
  cmdLine = """" & exeOrTag & """ """ & mainPy & """"
End If

' 0 = hidden window style; False = do not wait for the process to finish.
shell.Run cmdLine, 0, False
