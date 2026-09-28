"""Gera o "deploy_info.json" no próprio ambiente do Render, a cada start do
serviço (ver startCommand em render.yaml) - equivalente ao passo "Gerar
informações de versão/deploy" que o pipeline do GitHub Actions
(.github/workflows/deploy.yml) já faz para qualidade/main.

Contexto (Silvan, 2026-09-10): desde que o media-dev passou a rodar no
Render em vez do Elastic Beanstalk (decisão do Silvan, 2026-09-04, ver
render.yaml), esse pipeline do GitHub Actions parou de rodar para a branch
"dev" - o Render faz deploy direto, sem passar pelo GitHub Actions. Como
"deploy_info.json" só era gerado ali, o media-dev ficou sem essa
informação, e a tela de login (auth/login.html) parou de mostrar a versão
publicada e o "ver histórico" (histórico de versões, gravado na tabela
HistoricoDeploy - ver app/__init__.py:_registrar_deploy_atual). Este
script tampa esse buraco gerando o mesmo arquivo, só que localmente no
Render, usando o git do próprio checkout (o build do Render mantém o
".git").

Diferença importante em relação ao script do GitHub Actions: lá, dá para
comparar TODO o intervalo de commits de um push (github.event.before até
github.event.after), então um push com vários commits de uma vez aparece
inteiro no histórico. Aqui não temos esse "antes" (não é um webhook de
push, é só "que commit está de pé agora") - por isso este script usa só a
mensagem do commit mais recente (HEAD). Na prática isso quase não importa
pro dev: o fluxo normal aqui é um commit por vez (ver auto_commit_push.bat
na máquina do Silvan), não pacotes de vários commits de uma vez como
acontece ao promover dev -> qualidade/main.

Chamado automaticamente pelo startCommand do render.yaml, antes do
gunicorn subir - não precisa rodar manualmente. Se o git não estiver
disponível por qualquer motivo (não deveria acontecer no Render, mas por
segurança), falha em silêncio: a tela de login simplesmente não mostra a
versão, do mesmo jeito que já acontecia em desenvolvimento local antes
deste script existir (ver app/__init__.py:_carregar_info_deploy)."""
import datetime
import json
import subprocess


def _rodar(comando):
    try:
        return subprocess.check_output(comando, text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main():
    commit = _rodar(["git", "rev-parse", "HEAD"])
    if not commit:
        return  # sem git/sem repositório - não é um erro, só não gera o arquivo

    branch = _rodar(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "?"
    mensagem = _rodar(["git", "log", "-1", "--pretty=%s"]) or ""

    info = {
        "commit": commit,
        "commit_curto": commit[:7],
        "branch": branch,
        "mensagem": mensagem,
        "deploy_em": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    with open("deploy_info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    print(f"deploy_info.json gerado: commit {info['commit_curto']} ({branch}): {mensagem}")


if __name__ == "__main__":
    main()
