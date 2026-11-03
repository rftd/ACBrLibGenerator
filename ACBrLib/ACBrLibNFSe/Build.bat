@echo off
set lazBuild="E:\Programacao\Lazarus\lazarus\lazbuild.exe"
set libSource="E:\Programacao\ACBr\Fontes\Projetos\ACBrLib\Fontes\NFSe\ACBrLibNFSe.lpi"
set libBin="E:\Programacao\ACBr\Fontes\Projetos\ACBrLib\Fontes\NFSe\bin"

%lazBuild% -B --no-write-project --bm=Win32-i386-CDECL %libSource%
%lazBuild% -B --no-write-project --bm=Win64-x86_64-CDECL %libSource%
%lazBuild% -B --no-write-project --bm=Win32-i386-STDCALL %libSource%
%lazBuild% -B --no-write-project --bm=Win64-x86_64-STDCALL %libSource%
%lazBuild% -B --no-write-project --bm=Linux-x86_64 %libSource%
%lazBuild% -B --no-write-project --bm=Linux-i386 %libSource%
%lazBuild% -B --no-write-project --bm=Win32-i386-CDECL-MT %libSource%
%lazBuild% -B --no-write-project --bm=Win64-x86_64-CDECL-MT %libSource%
%lazBuild% -B --no-write-project --bm=Win32-i386-STDCALL-MT %libSource%
%lazBuild% -B --no-write-project --bm=Linux-x86_64-MT %libSource%
%lazBuild% -B --no-write-project --bm=Linux-i386-MT %libSource%

rmdir /s /q bin
robocopy %libBin% bin /E

rmdir /s /q log
mkdir log
copy "E:\Programacao\ACBr\Fontes\Projetos\ACBrLib\Fontes\NFSe\ACBrLibNFSe-change-log.txt" log
copy "E:\Programacao\ACBr\Fontes\Projetos\ACBrLib\Fontes\Comum\ACBrLibComum-change-log.txt" log
copy "E:\Programacao\ACBr\Fontes\Fontes\ACBrDFe\ACBrNFSe\ACBrNFSe-change-log.txt" log