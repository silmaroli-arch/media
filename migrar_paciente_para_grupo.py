"""Fatia 5 (passo 2) da migração para Grupo — unifica o cadastro de
paciente numa identidade GLOBAL única por CPF (ver Paciente em
app/models.py), com a associação real a cada clínica/grupo feita por
GrupoPaciente.

Duas fases, nessa ordem (idempotente, pode rodar de novo sem problema):

FASE A — cria os GrupoPaciente que faltam para cada cadastro (Paciente)
legado que ainda tem empresa_id/clinica_id preenchido (modelo antigo,
"paciente é da empresa"/"paciente é da filial"): associa esse paciente ao
Grupo pareado (ver Clinica.grupo_pareado(), da Fatia 4) de cada filial da
empresa dele (ou só a filial legada, se o cadastro nem chegou a ganhar
empresa_id). Sem isso, medico._filtro_pacientes_da_empresa() (que passa a
ler só GrupoPaciente a partir desta fatia) deixaria de enxergar esses
cadastros.

FASE B — deduplica por CPF: hoje é possível (modelo antigo) a mesma pessoa
ter um cadastro (Paciente) por empresa que frequenta. Para cada CPF
repetido, escolhe um cadastro sobrevivente (o mais antigo) e reaponta
Agendamento/PerguntaPendente/ChatMensagem/GrupoPaciente dos outros
cadastros para ele, preenchendo no sobrevivente os campos de contato/
endereço que estiverem em branco a partir dos duplicados - depois apaga os
cadastros redundantes.

Pré-requisito: rodar DEPOIS de migrar_grupo_por_clinica.py (toda Clinica
precisa já estar pareada com um Grupo) e de migrar_empresa_para_grupo.py
(não é uma dependência de dados, só a ordem lógica dos scripts da Fatia 5).

Como rodar (mesmo padrão dos outros scripts):
1. Configure a DATABASE_URL do ambiente que quer migrar (.env ou variável
   de ambiente).
2. Rode: python migrar_paciente_para_grupo.py
"""
import re

from app import create_app
from app.extensions import db
from app.models import Agendamento, ChatMensagem, Clinica, GrupoPaciente, Paciente, PerguntaPendente

app = create_app()

CAMPOS_PARA_PREENCHER_NO_SOBREVIVENTE = [
    "email", "telefone", "data_nascimento", "observacoes",
    "cep", "rua", "numero", "complemento", "bairro", "cidade", "uf",
    "contato_emergencia_nome", "contato_emergencia_telefone",
]


def _cpf_digitos(cpf):
    return re.sub(r"\D", "", cpf or "")


def _grupos_do_cadastro_legado(paciente):
    """Grupos que este cadastro (modelo antigo) implica - todas as
    filiais da empresa dele, ou só a filial legada se nunca ganhou
    empresa_id."""
    if paciente.empresa_id:
        clinicas = Clinica.query.filter_by(empresa_id=paciente.empresa_id).all()
    elif paciente.clinica_id:
        clinica = Clinica.query.get(paciente.clinica_id)
        clinicas = [clinica] if clinica else []
    else:
        clinicas = []
    return [c.grupo_pareado() for c in clinicas]


def _associar(grupo, paciente):
    if not GrupoPaciente.query.filter_by(grupo_id=grupo.id, paciente_id=paciente.id).first():
        db.session.add(GrupoPaciente(grupo_id=grupo.id, paciente_id=paciente.id))
        return True
    return False


with app.app_context():
    pacientes = Paciente.query.all()
    print(f"{len(pacientes)} cadastro(s) de paciente encontrado(s).")

    # ---------- Fase A: GrupoPaciente para cadastros legados ----------
    associacoes_criadas = 0
    for paciente in pacientes:
        for grupo in _grupos_do_cadastro_legado(paciente):
            if _associar(grupo, paciente):
                associacoes_criadas += 1
    db.session.commit()
    print(f"Fase A: {associacoes_criadas} associação(ões) GrupoPaciente criada(s) a partir de cadastros legados.")

    # ---------- Fase B: dedup por CPF ----------
    por_cpf = {}
    for paciente in Paciente.query.order_by(Paciente.id.asc()).all():
        por_cpf.setdefault(_cpf_digitos(paciente.cpf), []).append(paciente)

    grupos_repetidos = {cpf: lista for cpf, lista in por_cpf.items() if cpf and len(lista) > 1}
    print(f"{len(grupos_repetidos)} CPF(s) com mais de um cadastro (serão unificados).")

    removidos = 0
    for cpf, duplicados in grupos_repetidos.items():
        sobrevivente, *resto = duplicados  # o mais antigo (menor id), por causa do order_by acima

        for campo in CAMPOS_PARA_PREENCHER_NO_SOBREVIVENTE:
            if getattr(sobrevivente, campo) in (None, ""):
                for outro in resto:
                    valor = getattr(outro, campo)
                    if valor not in (None, ""):
                        setattr(sobrevivente, campo, valor)
                        break

        for outro in resto:
            Agendamento.query.filter_by(paciente_id=outro.id).update({"paciente_id": sobrevivente.id})
            PerguntaPendente.query.filter_by(paciente_id=outro.id).update({"paciente_id": sobrevivente.id})
            ChatMensagem.query.filter_by(paciente_id=outro.id).update({"paciente_id": sobrevivente.id})

            for gp in GrupoPaciente.query.filter_by(paciente_id=outro.id).all():
                if GrupoPaciente.query.filter_by(grupo_id=gp.grupo_id, paciente_id=sobrevivente.id).first():
                    db.session.delete(gp)  # já existe o mesmo vínculo no sobrevivente
                else:
                    gp.paciente_id = sobrevivente.id

            db.session.delete(outro)
            removidos += 1

    db.session.commit()
    print(f"Fase B: {removidos} cadastro(s) duplicado(s) removido(s) (dados unificados no cadastro mais antigo).")
