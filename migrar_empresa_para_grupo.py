"""Fatia 5 da migração para Grupo — copia os dados de cobrança, endereço e
fiscal de cada Empresa/Clinica existente para o Grupo já pareado a ela (ver
Clinica.grupo_pareado()/migrar_grupo_por_clinica.py, da Fatia 4).

Decisão de negócio da Fatia 5: a cobrança deixa de ser "por empresa com
várias filiais" e passa a ser POR GRUPO — cada Grupo (cada filial de hoje)
vira sua própria unidade de cobrança, com o mesmo valor_por_medico que a
empresa tinha (uma empresa com 2 filiais, que hoje gera 1 fatura só, passa
a gerar 2, uma por Grupo/filial).

Idempotente: usa `valor_por_medico` como sentinela — um Grupo que já tem
esse campo preenchido é considerado já migrado e é pulado inteiro (rodar de
novo não sobrescreve edições feitas depois direto no Grupo). Isso evita o
problema de campos NOT NULL com default (`status`, `fiscal_ambiente` etc.)
já virem preenchidos pelo próprio ALTER TABLE, o que tornaria um simples
"só se estiver em branco" campo-a-campo inútil para esses casos.

Pré-requisito: rodar ANTES o `migrar_grupo_por_clinica.py` (toda Clinica
precisa já estar pareada com um Grupo).

Como rodar (mesmo padrão dos outros scripts):
1. Rode ANTES o `python migrar_banco.py` (schema/colunas precisam existir)
   e o `python migrar_grupo_por_clinica.py` (pareamento Clínica<->Grupo).
2. Configure a DATABASE_URL do ambiente que quer migrar (.env ou variável
   de ambiente).
3. Rode: python migrar_empresa_para_grupo.py
"""
from app import create_app
from app.extensions import db
from app.models import Clinica

app = create_app()

CAMPOS_DA_CLINICA = [
    "razao_social", "cnpj", "email_contato", "telefone", "logo_url",
    "cep", "rua", "numero", "complemento", "bairro", "cidade", "uf",
    "inscricao_estadual", "regime_tributario", "cnae", "codigo_ibge_municipio",
    "fiscal_ambiente", "fiscal_modo_simulacao", "fiscal_simular_falha_conexao",
    "fiscal_certificado_pfx", "fiscal_certificado_senha_cripto",
    "fiscal_certificado_cnpj", "fiscal_certificado_validade",
    "fiscal_provedor_emissao", "fiscal_provedor_token_cripto",
    "fiscal_inscricao_municipal", "fiscal_codigo_servico", "fiscal_aliquota_iss",
    "fiscal_rps_serie", "fiscal_rps_proximo_numero", "codigo_cadastro_paciente",
]
CAMPOS_DA_EMPRESA = ["status", "data_vencimento", "observacoes_pagamento", "valor_por_medico"]

with app.app_context():
    clinicas = Clinica.query.all()
    print(f"{len(clinicas)} clínica(s) encontrada(s).")

    sem_grupo = [c for c in clinicas if not c.grupo_pareado_id]
    if sem_grupo:
        print(
            f"AVISO: {len(sem_grupo)} clínica(s) ainda não pareada(s) com um Grupo — "
            "rode migrar_grupo_por_clinica.py primeiro. Essas foram puladas."
        )

    atualizados, ja_migrados = 0, 0
    for clinica in clinicas:
        if not clinica.grupo_pareado_id:
            continue
        grupo = clinica.grupo_pareado_rel
        if grupo.valor_por_medico is not None:
            ja_migrados += 1
            continue

        empresa = clinica.empresa
        for campo in CAMPOS_DA_CLINICA:
            valor = getattr(clinica, campo)
            if valor not in (None, ""):
                setattr(grupo, campo, valor)
        for campo in CAMPOS_DA_EMPRESA:
            valor = getattr(empresa, campo)
            if valor not in (None, ""):
                setattr(grupo, campo, valor)
        atualizados += 1

    db.session.commit()
    print(f"{atualizados} grupo(s) atualizado(s) com dados de cobrança/endereço/fiscal.")
    if ja_migrados:
        print(f"{ja_migrados} grupo(s) já tinham sido migrados antes (pulados).")
