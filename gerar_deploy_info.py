"""Gera o arquivo "deploy_info.json" manualmente, para usar em deploys feitos
com "eb deploy" direto (bypass do GitHub Actions) - por exemplo, durante uma
indisponibilidade do GitHub como a que tivemos em 06/08/2026.

O pipeline automático (.github/workflows/deploy.yml) já gera esse arquivo
sozinho a cada deploy - você só precisa deste script quando for publicar
manualmente com "eb deploy".

Como rodar, ANTES de "eb deploy" (na pasta do projeto, já na branch que vai
publicar - main, qualidade ou dev):

    python gerar_deploy_info.py
    eb deploy media-prod        (ou media-qa / media-dev, conforme o caso)

Atenção: o "eb deploy" publica o código a partir do último commit da branch
(não da pasta local "como está"), porque o controle de versão está
configurado como "git" (veja .elasticbeanstalk/config.yml). Por isso este
script já comita e envia (push) esse arquivo para a branch atual - senão ele
não apareceria no ambiente publicado.
"""
import datetime
import json
import subprocess
import sys


def rodar(comando):
    return subprocess.check_output(comando, shell=True, text=True).strip()


try:
    commit = rodar("git rev-parse HEAD")
    branch = rodar("git rev-parse --abbrev-ref HEAD")
except subprocess.CalledProcessError:
    print("Erro ao consultar o Git - rode este script dentro da pasta do projeto.")
    sys.exit(1)

info = {
    "commit": commit,
    "commit_curto": commit[:7],
    "branch": branch,
    "deploy_em": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
}

with open("deploy_info.json", "w", encoding="utf-8") as f:
    json.dump(info, f, ensure_ascii=False, indent=2)

print(f"deploy_info.json gerado: commit {info['commit_curto']} ({branch}), {info['deploy_em']}")

subprocess.run("git add deploy_info.json -f", shell=True, check=True)
subprocess.run('git commit -m "Atualiza informacao de deploy (manual)"', shell=True, check=True)
subprocess.run(f"git push origin {branch}", shell=True, check=True)

print("Commitado e enviado. Agora pode rodar: eb deploy")
