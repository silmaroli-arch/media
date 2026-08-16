"""Fatia 4 da migração para Grupo — pareia cada Clinica (filial) existente
com um Grupo novo (Clinica.grupo_pareado()) e faz o backfill de grupo_id nas
linhas já existentes de Exame/PreparoModelo/Agendamento/PerguntaPendente/
FaqItem que ainda não têm esse campo preenchido.

Diferente de migrar_banco.py (que só faz ALTER TABLE/ADD COLUMN e roda
automaticamente em todo deploy), este script cria dados/objetos de negócio
de verdade (um Grupo + GrupoMembro por clínica) — por isso NÃO está
amarrado ao hook de predeploy: é rodado manualmente, uma vez, por quem
estiver de olho no resultado.

Idempotente: rodar de novo não duplica nada (Clinica.grupo_pareado() só
cria o grupo na primeira chamada; os UPDATEs de backfill só afetam linhas
com grupo_id IS NULL).

Como rodar (mesmo padrão do migrar_banco.py / seed.py):
1. Rode ANTES o `python migrar_banco.py` (schema/colunas precisam existir).
2. Configure a DATABASE_URL do ambiente que quer migrar (.env ou variável
   de ambiente), igual ao migrar_banco.py.
3. Rode: python migrar_grupo_por_clinica.py
"""
from app import create_app
from app.extensions import db
from app.models import Clinica, Exame, PreparoModelo, Agendamento, PerguntaPendente, FaqItem

app = create_app()

with app.app_context():
    clinicas = Clinica.query.all()
    print(f"{len(clinicas)} clínica(s) encontrada(s).")

    for clinica in clinicas:
        ja_pareada = clinica.grupo_pareado_id is not None
        grupo = clinica.grupo_pareado()
        db.session.flush()
        if not ja_pareada:
            print(f"Clínica '{clinica.nome}' (id={clinica.id}) pareada com o Grupo id={grupo.id}.")

        Exame.query.filter_by(clinica_id=clinica.id, grupo_id=None).update({"grupo_id": grupo.id})
        PreparoModelo.query.filter_by(clinica_id=clinica.id, grupo_id=None).update({"grupo_id": grupo.id})
        Agendamento.query.filter_by(clinica_id=clinica.id, grupo_id=None).update({"grupo_id": grupo.id})
        PerguntaPendente.query.filter_by(clinica_id=clinica.id, grupo_id=None).update({"grupo_id": grupo.id})
        FaqItem.query.filter_by(clinica_id=clinica.id, grupo_id=None).update({"grupo_id": grupo.id})

    db.session.commit()
    print("Pareamento Clínica ↔ Grupo e backfill de grupo_id concluídos.")
