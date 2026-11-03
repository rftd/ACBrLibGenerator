# ACBrLibGenerator

Automação para compilar **ACBrLibs**, **ACBrMonitor** e gerar os instaladores Windows do Monitor. Utiliza PascalInstall e ACBrInstall para preparar o ambiente, Docker para os builds Linux e um runner Windows com Inno Setup.

## Escopo inicial

| Perfil | Saídas |
|---|---|
| `linux64` | Bibliotecas x86-64 ST/MT (`.so`); Monitor somente no perfil Windows |
| `windows` | Bibliotecas x86/x64, Cdecl/StdCall, ST/MT (`.dll`), Monitor e instaladores x86/x64 |

Os projetos e modos são lidos dos arquivos `.lpi`. A seleção `all` inclui as bibliotecas com modos compatíveis e informa as ignoradas; uma seleção explícita incompatível retorna erro. Demos e projetos ConsoleMT separados não fazem parte desta primeira versão. Os modos antigos de GAV/LCB também são compilados no Windows, com CPU e sistema explícitos.

Cada execução cria uma pasta própria com binários, ZIPs, `build.log`, `manifest.json` e `SHA256SUMS`. O manifesto registra comandos, revisão SVN, arquivos gerados e falhas. O processo não executa `svn revert`, commits ou publicação Git.

## Configuração

- [config/toolchain.json](config/toolchain.json): commit dos instaladores, versões FPC/Lazarus, revisão SVN ACBr, cross-compilers e Inno Setup com SHA256.
- [config/mirror.json](config/mirror.json): origem SVN, branch `svn/trunk` e primeira revisão importada.
- [config/sources.lock.json](config/sources.lock.json): seleção opcional de SHA Git e revisão SVN para builds manuais. Releases fixam os fontes automaticamente no anexo `source-lock.json`.
- [config/builds.json](config/builds.json): bibliotecas, modos, Monitor e instaladores por perfil.

A imagem inicial usa FPC 3.2.2, Lazarus 4.6 e revisão ACBr 48919 como base de preparação. Nos builds do CI, os fontes são substituídos pelo commit Git selecionado do mirror em `/opt/acbr`; os pacotes são registrados e recompilados antes dos produtos. Atualizar os fontes não exige reconstruir o toolchain. Alterações no FPC/Lazarus ou cross-compilers exigem uma nova imagem.

A imagem usa as CLIs dos instaladores. Quando `--build-only` estiver disponível, dispensa adicionar OPM/designer e componentes à IDE; versões anteriores também configuram a IDE. Os compiladores são verificados com um programa de teste, incluindo cada alvo cross configurado.

O PascalInstall prepara o ambiente nativo. Os compiladores cross são construídos pelo `fpclazup` a partir desses mesmos fontes, sem repetir as opções de download que ele rejeita no modo cross. Qualquer falha interrompe a preparação antes de compilar os produtos.

## Uso local

Requer Python 3.11 ou posterior e um ambiente Lazarus/ACBr preparado. Nesta versão, a pasta de saída não pode conter espaços; a pasta dos fontes pode.

```bash
# Inspecionar projetos e modos
python3 scripts/acbrbuild.py inspect --sources /caminho/ACBr

# Conferir comandos sem compilar nem gravar arquivos
python3 scripts/acbrbuild.py plan --sources /caminho/ACBr \
  --environment /caminho/lazarus_dev --libraries ACBrLibCEP --no-monitor

# Compilar a biblioteca nos modos ST e MT do perfil Linux
python3 scripts/acbrbuild.py build --sources /caminho/ACBr \
  --environment /caminho/lazarus_dev --libraries ACBrLibCEP --no-monitor --output dist
```

Para compilar apenas o Monitor, use `--libraries "" --monitor`. `--lib-modes` e `--monitor-modes` substituem a seleção do perfil; `--incremental` desativa o `-B`. Consulte `--help` para caminhos personalizados de `lazbuild`, configuração Lazarus e Inno Setup.

No Windows, após preparar o ambiente:

```powershell
python scripts/acbrbuild.py build --profile windows --sources C:/acbr-src `
  --environment C:/acbr-env --libraries "" --monitor --installer `
  --iscc 'C:/Program Files (x86)/Inno Setup 6/ISCC.exe' --output dist
```

O instalador usa uma cópia do `.iss` oficial, adaptada para a arquitetura e o binário recém-compilado. Os arquivos originais são preservados. DLLs, schemas e demais arquivos do instalador vêm dos fontes ACBr; arquivos ausentes interrompem a geração.

## Docker

```bash
python3 scripts/docker_build.py --tag acbrlibgenerator:local
mkdir -p dist
docker run --rm --mount "type=bind,src=$PWD/dist,dst=/artifacts" \
  acbrlibgenerator:local build --profile linux64 --output /artifacts
```

O bind mount exige que a pasta exista no host do daemon Docker. Em Rancher Desktop/WSL ou daemons remotos, ajuste o compartilhamento de pastas ou use `docker cp` para transferir os resultados.

A construção inicial instala FPC/Lazarus e compila as dependências ACBr. É a etapa mais demorada; os builds seguintes reutilizam esse ambiente. Para verificar apenas os executáveis de preparação, use `--target base` no script de construção.

O ambiente contém os fontes ACBr na revisão configurada e as dependências resolvidas pelo catálogo OPM. O bootstrap do `fpclazup` e o catálogo OPM ainda são consultados pelos instaladores: fixar versões não torna duas reconstruções da imagem necessariamente idênticas. Para repetir um ambiente publicado, use a referência **por digest** da imagem, registrada pelo workflow.

## Mirror SVN e releases

A branch `main` contém a automação. A branch `svn/trunk` recebe os commits do ACBr via `git svn`, preservando autor, mensagem e `git-svn-id`. O fluxo é unidirecional: não faz `dcommit`, merge com `main` nem push forçado.

A importação começa na revisão **48919**, configurável antes da primeira execução. O histórico anterior não é importado. A primeira sincronização pode demorar; as seguintes são incrementais. O cache de metadados é reutilizado, mas pode ser reconstruído a partir da branch publicada. Arquivos de 100 MiB ou mais interrompem a publicação com uma mensagem explícita; o script não os remove silenciosamente do histórico.

Para sincronizar localmente, instale `git-svn` e Subversion:

```bash
python3 scripts/sync_mirror.py --repository https://github.com/rftd/ACBrLibGenerator.git
# Após conferir o estado, publique somente a branch do mirror:
python3 scripts/sync_mirror.py --repository https://github.com/rftd/ACBrLibGenerator.git --push
```

No GitHub, **Sincronizar SVN** executa somente por acionamento manual. Pode receber uma revisão numérica e disparar explicitamente o workflow de build. O push com `GITHUB_TOKEN` não é usado como gatilho implícito de outro workflow.

Para publicar uma release:

1. Envie a automação atualizada para `main`.
2. No GitHub, abra **Releases → Draft a new release** e crie uma tag com **Target: main**.
3. Preencha título e descrição e clique em **Publish release**.
4. Acompanhe **Gerar ACBrLib e Monitor** na aba **Actions**. Os arquivos serão anexados ao concluir os builds.

A publicação executa os testes, sincroniza o SVN na revisão mais recente, atualiza `svn/trunk` e fixa o SHA importado em `source-lock.json` na própria release antes de compilar. Também cria a imagem Linux quando necessário, compila as bibliotecas nas duas plataformas e gera os instaladores Windows do Monitor. Não é necessário sincronizar o mirror, preparar a imagem ou fazer commit do lock manualmente.

O SHA é selecionado uma única vez para os dois builds. Reexecutar uma release com `source-lock.json` já anexado reutiliza os mesmos fontes, mesmo que o SVN tenha avançado. Falhas ao consultar ou validar esse anexo interrompem o processo. A automação não altera `main` nem move a tag: ela deve apontar para o código do gerador, não para a branch pura dos fontes. Como a revisão é resolvida durante a execução, use uma tag própria para a publicação, como `build-2026-11-03`, sem presumir um número de revisão SVN.

O lock versionado pode continuar com `commit: null` para releases automáticas. Para fixar fontes usados em builds manuais, mantenha a opção local:

```bash
python3 scripts/resolve_sources.py --repository https://github.com/rftd/ACBrLibGenerator.git \
  --ref svn/trunk --pin
```

## GitHub Actions

| Workflow | Gatilho | Responsabilidade |
|---|---|---|
| [ci.yml](.github/workflows/ci.yml) | Manual ou chamado pelo build | Testes, integração com SVN temporário e verificação Docker |
| [mirror.yml](.github/workflows/mirror.yml) | Manual | Sincronizar `svn/trunk`; build opcional após a sincronização |
| [image.yml](.github/workflows/image.yml) | Manual ou chamado pelo build | Reutilizar ou publicar o toolchain Linux no GHCR |
| [build.yml](.github/workflows/build.yml) | Execução manual ou release publicada | Validar, preparar fontes e ambiente, compilar e anexar os resultados |

Para testar sem publicar, execute **Gerar ACBrLib e Monitor** com `source_ref=svn/trunk` ou um SHA completo após sincronizar o mirror. Nesse modo, deixar a referência vazia usa o lock versionado e não sincroniza o SVN automaticamente. A imagem Linux é preparada automaticamente; é possível selecionar uma imagem existente por digest. Execuções manuais não publicam arquivos em releases.

A imagem é identificada por um hash dos arquivos do ambiente e reutilizada quando disponível. Uma construção nova também publica as tags `sha-<commit>` e `toolchain-v1`. O build recebe a referência por digest e o resumo registra esse valor. Ela fornece os compiladores e dependências de terceiros. Os scripts atuais e os fontes selecionados são montados no contêiner; a seleção ACBr é recompilada para evitar reutilizar PPUs de outra revisão. No Windows, o mesmo commit espelhado é preparado antes do build e do Inno Setup.

O checkout verificado recebe uma adaptação de compatibilidade para revisões em que a ACBrLibDIS ainda chama os métodos antigos de conversão de strings. O gerador usa `ConverterStringEntrada`/`ConverterStringSaida` da classe base e registra o arquivo e seus hashes no `environment.json`. A adaptação preserva a codificação dos arquivos, é dispensada quando os métodos antigos ainda existem e não altera o SVN nem a branch do mirror.

Ao encerrar o contêiner, mesmo com falha de compilação, o gerador devolve a propriedade dos artefatos ao usuário do runner. Isso permite anexar manifestos e logs privados ao workflow para diagnóstico.

São usadas as permissões `contents: write` para atualizar o mirror, `actions: write` para disparar builds e `packages: write` para publicar a imagem. Credenciais não são gravadas no cache SVN/Git. A primeira publicação dos arquivos locais e a autorização de acesso ao repositório privado, se houver, são necessárias antes de executar os workflows.

## Desenvolvimento e validação

```bash
bash scripts/install_git_svn.sh  # versões compatíveis no Ubuntu/CI
python3 -m unittest discover -s tests -v
python3 -m compileall -q acbrlibgenerator scripts
docker build --check -f docker/Dockerfile .
```

Os testes usam projetos temporários e compiladores simulados para verificar seleção, isolamento de ST/MT, propagação de falhas, arquivos ausentes, checksums e adaptação do script Inno Setup. Uma compilação real exige os fontes e as ferramentas correspondentes; os testes não substituem a validação dos binários gerados.

Validação inicial concluída no Linux: ACBrLibCEP ST/MT e ACBrMonitor compilados tanto no ambiente local quanto dentro da imagem `acbrlibgenerator:local`. Os artefatos de verificação estão em `dist/validation-linux`, `dist/docker-validation` e `dist/docker-monitor-validation` (ignorados pelo Git). A geração dos instaladores Windows ainda precisa de validação no runner Windows.

## Pacotes e arquivos da release

Os gatilhos externos são manuais ou por publicação de release. O build chama os workflows de verificação e imagem internamente. Não há gatilhos de push, pull request ou agendamento.

A pasta [ACBrLib](ACBrLib/README.md), versionada em `main`, contém os modelos de distribuição importados: PDFs, INIs, schemas, logos e dependências de terceiros. Binários ACBr antigos e logs não são versionados nessa pasta. Os mapeamentos de [config/distribution.json](config/distribution.json) regeneram os logs, schemas, logos e INIs a partir da revisão selecionada. Os binários usados nos pacotes são sempre os da execução atual. `Build.bat` é preservado como referência, mas excluído dos ZIPs, como no FinalBuilder.

A publicação segue os nomes originais:

- `ACBrLibCEP 1.0.2.362.zip`: um ZIP por biblioteca, contendo suas variantes Windows e Linux disponíveis em `bin/`, além de `dep/`, `log/` e `LEIA-ME.pdf`.
- `ACBrMonitorPLUS-<versão>-x86-Instalador.exe` e `ACBrMonitorPLUS-<versão>-x64-Instalador.exe`: anexados individualmente; não há build nem publicação do Monitor Linux neste fluxo.
- `source-lock.json`, `release-manifest.json` e `SHA256SUMS`: revisão dos fontes e integridade dos arquivos publicados.

A versão de cada ZIP vem do recurso **FileVersion da DLL Windows compilada**, em quatro números, como no FinalBuilder; não usa `ProductVersion`, a versão do gerador ou a tag da release. Os ZIPs dos jobs são intermediários: a etapa final reúne as variantes por biblioteca e publica cada pacote diretamente, sem um ZIP geral por plataforma. Builds incompletos, versões Windows inconsistentes, arquivos ausentes ou checksums inválidos interrompem a publicação.

O gerador não baixa FastReport nem inclui integrações pagas por padrão. macOS, Linux i386 e bibliotecas ARM não fazem parte dos perfis iniciais. O fluxo de distribuição do Monitor está limitado ao Windows.

## Créditos e licença

Autor: **Rafael Teno DIas**. Código do gerador sob [licença MIT](LICENSE). Os fontes ACBr, bibliotecas, ferramentas e recursos de terceiros mantêm suas próprias licenças.

Referências: [ACBrInstall/PascalInstall](https://github.com/rftd/ACBrInstall), [Projeto ACBr](https://www.projetoacbr.com.br/), [Inno Setup](https://jrsoftware.org/). O fluxo foi baseado no projeto FinalBuilder `ACbr Builder.fbp8` e nos arquivos de build dos fontes ACBr; o banco de logs `.fbl8` não é necessário.
