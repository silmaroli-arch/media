"""Testa o registro de achados de um procedimento gastroenterológico
(colonoscopia, endoscopia, etc.) — qualidade de preparo, sedação, pólipos,
complicações, tempo de procedimento."""
from app import create_app
from app.extensions import db
from app.models import ProcedimentoGastro, ProcedimentoPolipo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Login como médico e seleciona empresa/filial
with app.app_context():
    from app.models import Agendamento, Clinica
    agendamento = Agendamento.query.first()
    agend_id = agendamento.id
    filial_id = agendamento.clinica_id
    empresa_id = agendamento.clinica.empresa_id

r = client.post("/login", data={"email": "medico@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)
r = client.post("/equipe/clinica", data={"empresa_id": empresa_id}, follow_redirects=True)

# ---------- Tela de achados do procedimento ----------

r = client.get(f"/equipe/agenda/{agend_id}/procedimento-gastro", follow_redirects=True)
checar("Tela de achados do procedimento carrega (GET)", r.status_code == 200)
html = r.get_data(as_text=True)
checar("Tela tem campo de qualidade de preparo", 'name="qualidade_preparo"' in html)
checar("Tela tem checkbox de sedação", 'name="sedacao_realizada"' in html)
checar("Tela tem campo de achados principais", 'name="achados_texto"' in html)
checar("Tela tem campo de número de pólipos", 'name="numero_polipos"' in html)
checar("Tela tem botão de adicionar pólipo", "Adicionar pólipo" in html)

# ---------- Salvar um procedimento com pólipos ----------

r = client.post(
    f"/equipe/agenda/{agend_id}/procedimento-gastro",
    data={
        "qualidade_preparo": "excelente",
        "sedacao_realizada": "true",
        "sedacao_tipo": "propofol",
        "sedacao_dose": "1.5mg/kg",
        "achados_texto": "Íngreme colônica com infiltração mucosa. Múltiplos pólipos.",
        "numero_polipos": "3",
        "polipos_removidos": "2",
        "biopsias_coletadas": "1",
        "resseccao_endoscopica": "true",
        "hemorragia_controlada": "false",
        "complicacoes": "",
        "tempo_procedimento_minutos": "25",
        "observacoes": "Procedimento sem intercorrências.",
        # Pólipos
        "polipo_localizacao[]": ["ceco", "colon descendente"],
        "polipo_tamanho[]": ["8", "5"],
        "polipo_paris[]": ["2a", "1p"],
        "polipo_acoes[]": ["removido", "removido"],
        "polipo_histopatologia[]": ["adenoma tubular", "pólipo hiperplásico"],
        "polipo_obs[]": ["", ""],
    },
    follow_redirects=True,
)
checar("POST de achados redireciona e volta pro atendimento", "Atendimento" in r.get_data(as_text=True))
checar("Sucesso ao salvar achados", "com sucesso" in r.get_data(as_text=True).lower())

# ---------- Verificar se foi salvo no banco ----------

with app.app_context():
    proc = ProcedimentoGastro.query.filter_by(agendamento_id=agend_id).first()
    checar("Procedimento foi salvo no banco", proc is not None)
    if proc:
        checar("Qualidade de preparo foi salva", proc.qualidade_preparo == "excelente")
        checar("Sedação foi registrada", proc.sedacao_realizada is True)
        checar("Tipo de sedação foi salvo", proc.sedacao_tipo == "propofol")
        checar("Número de pólipos foi salvo", proc.numero_polipos == 3)
        checar("Pólipos removidos foi salvo", proc.polipos_removidos == 2)
        checar("Ressecção endoscópica foi registrada", proc.resseccao_endoscopica is True)
        checar("Tempo de procedimento foi salvo", proc.tempo_procedimento_minutos == 25)
        checar("Procedimento tem 2 pólipos vinculados", len(proc.polipos) == 2)
        if len(proc.polipos) == 2:
            p1 = proc.polipos[0]
            checar("Primeiro pólipo tem localização", p1.localizacao == "ceco")
            checar("Primeiro pólipo tem tamanho", p1.tamanho_mm == 8)
            checar("Primeiro pólipo tem classificação Paris", p1.classificacao_paris == "2a")
            checar("Primeiro pólipo tem histopatologia", "adenoma tubular" in p1.histopatologia)

# ---------- Tela de atendimento mostra card de achados ----------

r = client.get(f"/equipe/agenda/{agend_id}/atendimento", follow_redirects=True)
checar("Tela de atendimento carrega", r.status_code == 200)
html = r.get_data(as_text=True)
checar("Card de achados do procedimento aparece", "Achados do Procedimento" in html)
checar("Resumo mostra número de pólipos", "Pólipos encontrados:</strong> 3" in html)

# ---------- Editar o procedimento ----------

r = client.post(
    f"/equipe/agenda/{agend_id}/procedimento-gastro",
    data={
        "qualidade_preparo": "adequado",  # mudou
        "sedacao_realizada": "false",  # mudou
        "sedacao_tipo": "",
        "sedacao_dose": "",
        "achados_texto": "Revisado: sem alterações relevantes.",  # mudou
        "numero_polipos": "2",  # diminuiu
        "polipos_removidos": "1",
        "biopsias_coletadas": "0",
        "resseccao_endoscopica": "false",
        "hemorragia_controlada": "false",
        "complicacoes": "Leve sangramento já controlado.",  # novo
        "tempo_procedimento_minutos": "20",
        "observacoes": "Versão 2 — editado.",
        # Agora só um pólipo
        "polipo_localizacao[]": ["reto"],
        "polipo_tamanho[]": ["3"],
        "polipo_paris[]": ["1p"],
        "polipo_acoes[]": ["removido"],
        "polipo_histopatologia[]": ["carcinoma"],
        "polipo_obs[]": [""],
    },
    follow_redirects=True,
)
checar("POST de edição redireciona", "Atendimento" in r.get_data(as_text=True))

# Verifica os novos dados
with app.app_context():
    proc = ProcedimentoGastro.query.filter_by(agendamento_id=agend_id).first()
    checar("Qualidade foi atualizada", proc.qualidade_preparo == "adequado")
    checar("Sedação foi removida", proc.sedacao_realizada is False)
    checar("Achados foram atualizados", "Revisado" in proc.achados_texto)
    checar("Complicações foram adicionadas", "sangramento" in proc.complicacoes.lower())
    checar("Número de pólipos agora é 2", proc.numero_polipos == 2)
    checar("Procedimento agora tem 1 pólipo", len(proc.polipos) == 1)
    if len(proc.polipos) == 1:
        p = proc.polipos[0]
        checar("Pólipo restante é do reto", p.localizacao == "reto")
        checar("Histopatologia foi atualizada para carcinoma", "carcinoma" in p.histopatologia)

client.get("/logout")
print("\nTodos os testes de procedimento gastro passaram.")
