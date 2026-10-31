# Creates or updates the OSSI shortcut on the Desktop. Run it again if you move the repo.
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = (Get-Command pythonw -ErrorAction Stop).Source
$path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'OSSI.lnk'

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$repo\OSSI.pyw`""
$shortcut.WorkingDirectory = $repo
$shortcut.IconLocation = "$repo\src\ossi\assets\ossi.ico,0"
$shortcut.Description = 'OSSI personal assistant'
$shortcut.Save()

Write-Output "Shortcut saved to $path"
