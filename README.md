# ACBrLibGenerator

Automação para compilar **ACBrLibs**, **ACBrMonitor** e gerar os instaladores Windows do Monitor. Utiliza PascalInstall e ACBrInstall para preparar o ambiente, Docker para os builds Linux e um runner Windows com Inno Setup.

## Escopo inicial

| Perfil | Saídas |
|---|---|
| `linux64` | Bibliotecas x86-64 ST/MT (`.so`) e ACBrMonitor |
| `windows` | Bibliotecas x86/x64, Cdecl/StdCall, ST/MT (`.dll`), Monitor e instaladores x86/x64 |

Os projetos e modos são lidos dos arquivos `.lpi`. A seleção `all` inclui as bibliotecas com modos compatíveis e informa as ignoradas; uma seleção explícita incompatível retorna erro. Demos e projetos ConsoleMT separados não fazem parte desta primeira versão.

Cada execução cria uma pasta própria com binários, ZIPs, `build.log`, `manifest.json` e `SHA256SUMS`. O manifesto registra comandos, revisão SVN, arquivos gerados e falhas. O processo não executa `svn revert`, commits ou publicação Git.

## Configuração

- [config/toolchain.json](config/toolchain.json): commit dos instaladores, versões FPC/Lazarus, revisão SVN ACBr, cross-compilers e Inno Setup com SHA256.
- [config/mirror.json](config/mirror.json): origem SVN, branch `svn/trunk` e primeira revisão importada.
- [config/sources.lock.json](config/sources.lock.json): SHA Git e revisão SVN para releases reproduzíveis.
- [config/builds.json](config/builds.json): bibliotecas, modos, Monitor e instaladores por perfil.

A imagem inicial usa FPC 3.2.2, Lazarus 4.6 e revisão ACBr 48919 como base de preparação. Nos builds do CI, os fontes são substituídos pelo commit Git selecionado do mirror em `/opt/acbr`; os pacotes são registrados e recompilados antes dos produtos. Atualizar os fontes não exige reconstruir o toolchain. Alterações no FPC/Lazarus ou cross-compilers exigem uma nova imagem.

A imagem usa as CLIs dos instaladores. Quando `--build-only` estiver disponível, dispensa adicionar OPM/designer e componentes à IDE; versões anteriores também configuram a IDE. Os compiladores são verificados com um programa de teste, incluindo cada alvo cross configurado.

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

No GitHub, **Sincronizar SVN** executa a cada seis horas ou manualmente. Pode receber uma revisão numérica e disparar explicitamente o workflow de build. O push com `GITHUB_TOKEN` não é usado como gatilho implícito de outro workflow.

Para preparar uma release:

1. Sincronize `svn/trunk`.
2. Fixe o SHA e a revisão no arquivo de configuração:

   ```bash
   python3 scripts/resolve_sources.py --repository https://github.com/rftd/ACBrLibGenerator.git \
     --ref svn/trunk --pin
   ```

3. Confira e faça commit de `config/sources.lock.json` em `main`.
4. Crie uma tag nesse commit da automação, como `acbr-r48919`, e publique a release.

O lock começa com `commit: null`: a primeira seleção deve ser feita após publicar o mirror. Releases sem um SHA fixado falham antes de compilar. A tag deve apontar para a automação em `main`, não para a branch pura dos fontes. Os dois builds usam o mesmo SHA; `source-lock.json` é anexado à release junto dos pacotes.

## GitHub Actions

| Workflow | Gatilho | Responsabilidade |
|---|---|---|
| [ci.yml](.github/workflows/ci.yml) | Push em `main` e pull request | Testes, integração com SVN temporário e verificação Docker |
| [mirror.yml](.github/workflows/mirror.yml) | Agendamento ou execução manual | Sincronizar `svn/trunk`; build opcional após a sincronização |
| [image.yml](.github/workflows/image.yml) | Mudanças no ambiente em `main` ou execução manual | Publicar o toolchain Linux no GHCR |
| [build.yml](.github/workflows/build.yml) | Execução manual ou release publicada | Resolver o SHA do mirror, preparar pacotes, compilar e anexar os resultados |

Publique primeiro os arquivos da automação em `main`. Execute **Sincronizar SVN** e **Imagem de compilação**. Depois execute **Gerar ACBrLib e Monitor** com `source_ref=svn/trunk` ou um SHA completo. Deixar a referência vazia usa o lock versionado. Para Linux, é possível informar uma imagem por digest.

A imagem é publicada com as tags `sha-<commit>` e `toolchain-v1`; o resumo mostra seu digest. Ela fornece os compiladores e dependências de terceiros. Os scripts atuais e os fontes selecionados são montados no contêiner; a seleção ACBr é recompilada para evitar reutilizar PPUs de outra revisão. No Windows, o mesmo commit espelhado é preparado antes do build e do Inno Setup.

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

O gerador não baixa FastReport nem inclui integrações pagas por padrão. macOS, Linux i386 e bibliotecas ARM não fazem parte dos perfis iniciais. O ZIP do Monitor Linux contém o executável e seu changelog; ainda não é um instalador nem reúne todos os recursos de execução.

## Créditos e licença

Autor: **Rafael Teno DIas**. Código do gerador sob [licença MIT](LICENSE). Os fontes ACBr, bibliotecas, ferramentas e recursos de terceiros mantêm suas próprias licenças.

Referências: [ACBrInstall/PascalInstall](https://github.com/rftd/ACBrInstall), [Projeto ACBr](https://www.projetoacbr.com.br/), [Inno Setup](https://jrsoftware.org/). O fluxo foi baseado no projeto FinalBuilder `ACbr Builder.fbp8` e nos arquivos de build dos fontes ACBr; o banco de logs `.fbl8` não é necessário.
