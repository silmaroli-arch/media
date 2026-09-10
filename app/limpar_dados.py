"""Apagar TODOS os dados operacionais da plataforma de uma vez - pedido
explícito do Silvan (2026-09-10), depois de recadastrar repetidamente uma
conta de teste (Bruno Pavan) e acumular pacientes de teste órfãos (ver
routes_medico._paciente_teste_do_medico) até o ponto de preferir zerar o
ambiente inteiro em vez de continuar depurando dado por dado.

ATENÇÃO - histórico deste recurso (leia antes de mexer aqui): existiu uma
tela assim antes, "Limpar dados de teste", que apagava o banco inteiro SEM
LOGIN nenhum. Ela foi removida de propósito e substituída por
app/exclusao_usuario.py (exclusão de UMA conta por vez, escopada, com
senha) - decisão do próprio Silvan, por ser mais seguro. Ao trazer essa
funcionalidade de volta (2026-09-10), o Silvan foi avisado explicitamente
dessa decisão anterior e confirmou que queria mesmo assim, inclusive
disponível em TODOS os ambientes (incluindo media-prod, com dados reais de
clínicas pagantes) - não restrinja por ambiente sem confirmar de novo com
ele, e não facilite ainda mais o acesso a este recurso (ex.: atalho de
teclado, link fora do menu "zona de risco") sem alinhar antes.

Proteções mantidas por decisão do Silvan (ver dono.limpar_dados_banco em
routes_dono.py): exige a senha do dono que está executando a ação (mesmo
padrão de app.exclusao_usuario.usuario_excluir) E a digitação literal da
palavra "APAGAR TUDO" - duas confirmações, porque isso não tem volta.

O que este módulo NÃO apaga (de propósito):
- A conta do PRÓPRIO dono que está executando a ação (senão ele ficaria
  trancado para fora do próprio sistema depois de usar o recurso).
- PlataformaConfig (configuração global da instância - trial_dias, preço
  padrão de licença etc. - não é "dado operacional", é configuração da
  própria instalação).
- HistoricoDeploy (histórico de versões mostrado na tela de login - não é
  dado clínico/operacional, e apagar removeria o próprio rastro de quando
  esta limpeza foi publicada)."""
from app.extensions import db
from app.models import (
    Agendamento,
    ChamadaIA,
    ChatMensagem,
    ConversaWhatsapp,
    Exame,
    FaqItem,
    Grupo,
    GrupoConvite,
    GrupoMembro,
    GrupoPaciente,
    LicencaPagamento,
    Medicamento,
    Paciente,
    PerguntaPendente,
    PreparoAlimento,
    PreparoCorte,
    PreparoExameAnterior,
    PreparoInfoGeral,
    PreparoMedicamentoMantido,
    PreparoMedicamentoSuspenso,
    PreparoModelo,
    PushSubscription,
    ResultadoExame,
    Usuario,
    exame_medicos_associados,
)


def apagar_todos_os_dados(dono_atual):
    """Apaga TUDO relacionado a médicos, secretárias, pacientes, grupos,
    exames, preparos, agendamentos, conversas e histórico de IA - mantém
    só as contas de dono da plataforma (a que está executando a ação e
    qualquer outra que exista) e a configuração global da instância. NÃO
    faz commit - quem chamar decide quando salvar, depois de já ter
    validado senha e a frase de confirmação."""
    # 1. Pontas soltas / histórico sem mais nada dependendo dele.
    PushSubscription.query.delete(synchronize_session=False)
    LicencaPagamento.query.delete(synchronize_session=False)
    ChamadaIA.query.delete(synchronize_session=False)
    ResultadoExame.query.delete(synchronize_session=False)
    ChatMensagem.query.delete(synchronize_session=False)
    ConversaWhatsapp.query.delete(synchronize_session=False)
    PerguntaPendente.query.delete(synchronize_session=False)
    FaqItem.query.delete(synchronize_session=False)

    # 2. Agendamentos (o ResultadoExame ligado a cada um já foi acima).
    Agendamento.query.delete(synchronize_session=False)

    # 3. Conteúdo de preparo (dependem de PreparoModelo).
    PreparoCorte.query.delete(synchronize_session=False)
    PreparoInfoGeral.query.delete(synchronize_session=False)
    PreparoAlimento.query.delete(synchronize_session=False)
    PreparoExameAnterior.query.delete(synchronize_session=False)
    PreparoMedicamentoSuspenso.query.delete(synchronize_session=False)
    PreparoMedicamentoMantido.query.delete(synchronize_session=False)
    PreparoModelo.query.delete(synchronize_session=False)

    # 4. Associação médico<->exame (tabela simples, sem model próprio) e
    # os exames em si.
    db.session.execute(exame_medicos_associados.delete())
    Exame.query.delete(synchronize_session=False)

    # 5. Grupos e tudo ligado a eles.
    GrupoConvite.query.delete(synchronize_session=False)
    GrupoPaciente.query.delete(synchronize_session=False)
    GrupoMembro.query.delete(synchronize_session=False)
    Grupo.query.delete(synchronize_session=False)

    # 6. Pacientes (inclusive os "de teste", ver Paciente.eh_teste).
    Paciente.query.delete(synchronize_session=False)

    # 7. Catálogo de medicamentos (cadastrado pela equipe, não é dado de
    # nenhuma pessoa específica - mas ainda é "dado operacional").
    Medicamento.query.delete(synchronize_session=False)

    # 8. Por fim, todo Usuario que NÃO é dono da plataforma (médico e
    # secretária) - dono(s) ficam de pé, incluindo quem está executando
    # esta ação agora.
    Usuario.query.filter(Usuario.tipo != "dono").delete(synchronize_session=False)

    db.session.flush()
