"""Conteúdo INICIAL da base de conhecimento compartilhada (a "terceira IA",
pedido do Silvan, 2026-09-29) - carga direta no banco, sem planilha.

Perguntas frequentes de preparo pesquisadas em fontes públicas e
REESCRITAS com palavras próprias (nada copiado literalmente), sempre com a
fonte registrada. Regras de conteúdo (mesmas do prompt das IAs):
- só o que é do PREPARO/dia do exame, sem diagnóstico nem indicação clínica
- NUNCA prazo, hora ou dose específica de medicamento (isso é do preparo
  cadastrado pelo médico, que sempre vence)
- sem a frase "confirme com o seu médico" (a resposta já passa pelo médico)
Todos entram com origem "internet" e revisado=False: o dono confere em
/dono/base-conhecimento e marca como revisado.

Cuidado: não use ponto e vírgula em textos que possam ir parar em
migrar_banco.py (o parser dele quebra o SQL por ponto e vírgula) - aqui os
textos são inseridos pelo ORM, mas mantenha o hábito."""

FONTE_MAE_DE_DEUS = ("Hospital Mãe de Deus - Colonoscopia: indicações, preparo e dúvidas",
                     "https://www.maededeus.com.br/Blog/Artigo/colonoscopia-quando-fazer-como-e-o-preparo-e-principais-duvidas")
FONTE_PESTANA = ("Dr. Roberto Pestana - Tire suas dúvidas sobre o exame de colonoscopia",
                 "https://robertopestana.com.br/conheca-o-exame-de-colonoscopia/")
FONTE_OKAZAKI = ("Clínica Okazaki - Preparo para Colonoscopia",
                 "https://www.clinicaokazaki.com/preparo-colonoscopia")

FONTE_EDA_DIB = ("Instituto Victor Dib - Preparo para endoscopia: jejum, sedação e o dia do exame",
                 "https://institutovictordib.com.br/artigos/preparo-para-endoscopia")
FONTE_EDA_SEDIG = ("Sedig - Perguntas frequentes sobre endoscopia digestiva",
                   "https://sedig.med.br/faq-sedig/")
FONTE_EDA_DASA = ("Nav (Dasa) - Endoscopia digestiva alta: tudo sobre o exame",
                  "https://nav.dasa.com.br/blog/endoscopia")

FONTE_RM_DOCTORALIA = ("Doctoralia - Preparo para ressonância magnética: o que fazer e o que não fazer",
                       "https://www.doctoralia.com.br/blog/preparo-para-ressonancia-magnetica")
FONTE_TC_MAISLAUDO = ("Mais Laudo - Tomografia com contraste: o que é e como é realizado",
                      "https://maislaudo.com.br/blog/tomografia-com-contraste/")
FONTE_US_INSTITUTO = ("Instituto da Imagem - Ultrassonografia abdominal total: perguntas e respostas",
                      "https://www.instituto.med.br/ultrassonografia-abdominal-total-perguntas-e-respostas/")
FONTE_SANGUE_DB = ("Diagnósticos do Brasil - Jejum para exames de sangue",
                   "https://www.diagnosticosdobrasil.com.br/artigo/jejum-para-exames-de-sangue")

# (nome do tipo de exame, pergunta, resposta, (fonte_nome, fonte_url))
ITENS_PADRAO = [
    ("Colonoscopia", "A colonoscopia dói?",
     "O exame costuma ser feito com sedação, então em geral você não sente dor durante o procedimento. "
     "Depois, é comum ficar com gases, uma cólica leve e sonolência, que passam sozinhos.",
     FONTE_MAE_DE_DEUS),
    ("Colonoscopia", "Quanto tempo demora o exame?",
     "O procedimento em si costuma levar de 20 a 40 minutos, podendo demorar mais se houver biópsia ou "
     "retirada de pólipos. Depois há um tempo de repouso para a sedação passar.",
     FONTE_MAE_DE_DEUS),
    ("Colonoscopia", "Posso dirigir depois da colonoscopia?",
     "Não no mesmo dia, porque a sedação diminui os reflexos. O normal é voltar às atividades no dia seguinte.",
     FONTE_MAE_DE_DEUS),
    ("Colonoscopia", "Preciso de acompanhante para fazer a colonoscopia?",
     "Sim. Como o exame é feito com sedação, é necessário ir acompanhado de alguém que possa levar você de volta para casa.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Posso beber água durante o preparo?",
     "Sim, e é importante beber bastante líquido claro, porque o laxante faz o corpo perder muita água. "
     "A restrição total de líquidos só vale para o período final antes do exame, conforme as instruções do seu preparo.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Como sei se o preparo do intestino deu certo?",
     "As evacuações vão ficando cada vez mais líquidas, até saírem como um líquido amarelado e transparente, sem resíduos sólidos.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "O laxante não fez efeito, o que eu faço?",
     "Aguarde mais um tempo e continue bebendo líquidos claros. Se mesmo assim não houver efeito, avise a clínica antes do exame, "
     "porque um intestino mal limpo pode impedir o exame ou obrigar a repeti-lo.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Posso trocar o laxante por um remédio caseiro ou por outro?",
     "Não. Laxantes caseiros ou alternativos não têm a força nem a previsibilidade necessárias para limpar bem o intestino. "
     "Use apenas o laxante indicado no seu preparo.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Posso tomar meus remédios de rotina no dia do exame?",
     "Remédios de uso contínuo como os de pressão, tireoide e antidepressivos costumam ser mantidos, com um pequeno gole de água, "
     "a menos que o seu preparo diga o contrário.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Sou diabético, como faço com o jejum e a medicação?",
     "Os medicamentos para diabetes costumam precisar de ajuste por causa da dieta e do jejum. Avise no agendamento que você é "
     "diabético e não altere as doses por conta própria.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Uso anticoagulante, posso continuar tomando?",
     "Anticoagulantes podem precisar ser suspensos antes do exame por causa do risco de sangramento, mas a suspensão só deve seguir "
     "a orientação do seu preparo ou de quem prescreveu o remédio. Nunca pare por conta própria.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Uso Ozempic, Wegovy ou canetas parecidas, isso atrapalha o exame?",
     "Esses medicamentos (análogos de GLP-1) atrasam o esvaziamento do estômago e aumentam o risco de aspiração durante a sedação, "
     "por isso costumam precisar ser suspensos antes do exame. Informe o uso no agendamento e siga a orientação do seu preparo.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Posso mascar chiclete ou chupar bala durante o preparo?",
     "Não. Chiclete e balas contam como quebra da restrição alimentar do preparo.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Posso escovar os dentes no dia do exame?",
     "Sim, pode escovar os dentes normalmente, apenas evite engolir água.",
     FONTE_OKAZAKI),
    ("Colonoscopia", "Tomei um gole de água sem querer durante o jejum, o exame é cancelado?",
     "Um gole acidental geralmente não cancela o exame, mas avise a equipe da clínica para que ela decida.",
     FONTE_OKAZAKI),
    # ---- Endoscopia digestiva alta (carga 2, 2026-09-29) ----
    ("Endoscopia digestiva alta", "A endoscopia dói?",
     "O exame é feito com anestésico em spray na garganta e, em geral, sedação leve, então costuma ser bem tolerado. "
     "Você pode sentir um pouco de pressão ou vontade de engasgar, mas não dor forte.",
     FONTE_EDA_SEDIG),
    ("Endoscopia digestiva alta", "Preciso estar em jejum para a endoscopia?",
     "Sim. O estômago precisa estar vazio para o médico enxergar bem e para a sedação ser segura. "
     "Siga o tempo de jejum indicado no seu preparo, sem comer nem tomar nada além do que ele permitir.",
     FONTE_EDA_DIB),
    ("Endoscopia digestiva alta", "Posso tomar água antes da endoscopia?",
     "Depende da regra do seu preparo. Algumas clínicas permitem pequenos goles de água até perto do exame e outras pedem jejum total. "
     "Siga exatamente o que está no seu preparo.",
     FONTE_EDA_DASA),
    ("Endoscopia digestiva alta", "Posso tomar meus remédios de rotina no dia da endoscopia?",
     "Muitos remédios de uso contínuo são mantidos com um pequeno gole de água, mas alguns precisam de ajuste. "
     "Siga a lista do seu preparo e nunca suspenda um remédio por conta própria.",
     FONTE_EDA_DIB),
    ("Endoscopia digestiva alta", "Sou diabético, como faço com o jejum e os remédios?",
     "O jejum muda a necessidade de insulina e de outros remédios para diabetes, por isso eles costumam precisar de ajuste. "
     "Avise no agendamento que você é diabético e siga a orientação do seu preparo.",
     FONTE_EDA_DIB),
    ("Endoscopia digestiva alta", "Uso anticoagulante, preciso parar antes da endoscopia?",
     "Anticoagulantes e antiplaquetários podem precisar de ajuste, principalmente se houver chance de biópsia. "
     "Só suspenda se o seu preparo ou quem prescreveu o remédio mandar, nunca por conta própria.",
     FONTE_EDA_DIB),
    ("Endoscopia digestiva alta", "Preciso de acompanhante para a endoscopia?",
     "Sim, se o exame for feito com sedação. Você precisa de um adulto para levar você de volta para casa.",
     FONTE_EDA_DASA),
    ("Endoscopia digestiva alta", "Posso dirigir depois da endoscopia?",
     "Não no mesmo dia, porque a sedação deixa os reflexos mais lentos. Também evite operar máquinas ou tomar decisões importantes até a sedação passar completamente.",
     FONTE_EDA_SEDIG),
    ("Endoscopia digestiva alta", "Quando posso comer depois da endoscopia?",
     "Depois que o efeito do anestésico da garganta passar e você estiver bem acordado, a alimentação costuma ser liberada, começando por algo leve. "
     "Se foi feita biópsia ou outro procedimento, siga a orientação que a equipe der.",
     FONTE_EDA_SEDIG),
    ("Endoscopia digestiva alta", "Posso mascar chiclete, fumar ou chupar bala antes da endoscopia?",
     "Não. Chiclete, balas e cigarro estimulam o estômago e contam como quebra do jejum do preparo.",
     FONTE_EDA_DIB),
    ("Endoscopia digestiva alta", "Posso usar dentadura ou prótese dentária na endoscopia?",
     "Próteses removíveis, dentaduras, óculos e adereços costumam precisar ser retirados antes do exame. "
     "Avise a equipe se você usa qualquer um deles.",
     FONTE_EDA_DASA),
    ("Endoscopia digestiva alta", "É normal sentir a garganta estranha ou sono depois da endoscopia?",
     "Sim. Garganta anestesiada ou levemente irritada, sensação de barriga inchada, gases e sonolência são comuns e melhoram ao longo do dia.",
     FONTE_EDA_DIB),
    ("Endoscopia digestiva alta", "Quais sintomas depois da endoscopia exigem contato com a equipe?",
     "Dor forte na barriga ou no peito, vômito com sangue, fezes muito escuras, febre ou dificuldade para engolir ou respirar "
     "são sinais de alerta. Procure a equipe ou um pronto atendimento imediatamente.",
     FONTE_EDA_DIB),
    # ---- Ressonância, tomografia, ultrassom de abdome e exames de sangue (carga 3, 2026-09-29) ----
    ("Ressonância magnética com contraste", "Preciso de jejum para a ressonância?",
     "Depende do exame. Quando há contraste ou sedação, costuma ser pedido jejum, e sem contraste muitas vezes não. Siga o que está no seu preparo.",
     FONTE_RM_DOCTORALIA),
    ("Ressonância magnética com contraste", "Posso fazer ressonância com marcapasso, prótese ou implante metálico?",
     "Depende do tipo e do modelo do dispositivo, porque o campo magnético pode movê-lo ou aquecê-lo. Informe no agendamento e leve o cartão ou laudo do implante se tiver.",
     FONTE_RM_DOCTORALIA),
    ("Ressonância magnética com contraste", "Que roupa devo usar na ressonância? Preciso tirar joias?",
     "Use roupa confortável, sem zíper, botão metálico ou fecho de sutiã com metal. Deixe em casa joias, relógio, piercings, celular, cartões e chaves. Maquiagem e produtos de cabelo com partículas metálicas também devem ser evitados.",
     FONTE_RM_DOCTORALIA),
    ("Ressonância magnética com contraste", "Tenho medo de lugar fechado, posso fazer a ressonância?",
     "Sim, muita gente com claustrofobia consegue fazer. Avise a equipe com antecedência, porque existem recursos como fones, espelhos, aparelhos mais abertos e, quando indicado, sedação.",
     FONTE_RM_DOCTORALIA),
    ("Ressonância magnética com contraste", "A ressonância faz barulho? Preciso ficar parada?",
     "Sim, o aparelho faz batidas altas, por isso a equipe oferece protetor de ouvido ou fone. Ficar imóvel é essencial, porque o movimento borra as imagens e pode obrigar a repetir a sequência.",
     FONTE_RM_DOCTORALIA),
    ("Ressonância magnética com contraste", "Estou grávida ou posso estar, posso fazer ressonância com contraste?",
     "Avise a equipe antes do exame. A ressonância em si costuma ser considerada segura, mas o contraste na gravidez só é usado quando o médico julga que o benefício compensa.",
     FONTE_RM_DOCTORALIA),
    ("Tomografia computadorizada com contraste", "Preciso de jejum para a tomografia com contraste?",
     "Em geral sim, para reduzir o risco de enjoo e vômito durante a injeção do contraste. O tempo de jejum é o indicado no seu preparo, e a água costuma ser permitida se ele não disser o contrário.",
     FONTE_TC_MAISLAUDO),
    ("Tomografia computadorizada com contraste", "Tenho alergia a contraste ou a iodo, posso fazer a tomografia?",
     "Avise a equipe antes do exame. Quem já teve reação a contraste pode precisar de preparo com medicação prévia ou de outro tipo de exame, e isso é decidido pelo médico.",
     FONTE_TC_MAISLAUDO),
    ("Tomografia computadorizada com contraste", "Tenho problema nos rins, o contraste da tomografia é seguro?",
     "O contraste iodado é eliminado pelos rins, por isso a função renal costuma ser avaliada com exame de sangue antes. Informe se você tem doença renal e leve seus exames recentes.",
     FONTE_TC_MAISLAUDO),
    ("Tomografia computadorizada com contraste", "Uso metformina, preciso parar antes da tomografia com contraste?",
     "Alguns protocolos pedem suspensão temporária da metformina em torno do exame, conforme a função renal. Siga a orientação do seu preparo e não altere por conta própria.",
     FONTE_TC_MAISLAUDO),
    ("Tomografia computadorizada com contraste", "O que se sente quando o contraste é injetado?",
     "É comum sentir calor pelo corpo, gosto metálico na boca ou vontade de urinar por instantes. Passa rápido. Avise a equipe se sentir coceira, falta de ar ou inchaço.",
     FONTE_TC_MAISLAUDO),
    ("Tomografia computadorizada com contraste", "O que devo fazer depois da tomografia com contraste?",
     "Beba bastante água ao longo do dia para ajudar os rins a eliminar o contraste, e volte às atividades normalmente. Se aparecerem manchas, coceira ou mal-estar, procure atendimento.",
     FONTE_TC_MAISLAUDO),
    ("Ultrassonografia de abdome total", "Preciso de jejum para o ultrassom de abdome?",
     "Em geral sim, porque o estômago e o intestino cheios atrapalham a visualização e a vesícula fica mais bem vista em jejum. Siga o tempo indicado no seu preparo.",
     FONTE_US_INSTITUTO),
    ("Ultrassonografia de abdome total", "Posso beber água antes do ultrassom de abdome?",
     "Sim, na quantidade que o seu preparo indicar. Em alguns casos a bexiga cheia é necessária para ver bem os órgãos e o preparo pede que você beba líquido antes do exame.",
     FONTE_US_INSTITUTO),
    ("Ultrassonografia de abdome total", "O que devo comer no dia anterior ao ultrassom de abdome para evitar gases?",
     "Prefira refeições leves, como sopa de legumes, frutas e chá, e evite refrigerante, leite, frituras, doces e alimentos que causam gases. Gases atrapalham a imagem.",
     FONTE_US_INSTITUTO),
    ("Exames de sangue com jejum (glicemia, perfil lipídico e outros)", "Posso beber água no jejum do exame de sangue?",
     "Sim, água pura em quantidade moderada não quebra o jejum e ajuda na coleta.",
     FONTE_SANGUE_DB),
    ("Exames de sangue com jejum (glicemia, perfil lipídico e outros)", "Posso tomar café no jejum do exame de sangue?",
     "Não. Café, mesmo sem açúcar, é considerado quebra de jejum. Chiclete e balas, inclusive os sem açúcar, também.",
     FONTE_SANGUE_DB),
    ("Exames de sangue com jejum (glicemia, perfil lipídico e outros)", "Posso tomar meus remédios de rotina no jejum do exame de sangue?",
     "Em geral sim, com água, a menos que o seu preparo ou o médico mande o contrário. Alguns remédios podem ser tomados só depois da coleta, então siga o seu preparo.",
     FONTE_SANGUE_DB),
    ("Exames de sangue com jejum (glicemia, perfil lipídico e outros)", "Posso fazer exercício antes do exame de sangue?",
     "Evite atividade física intensa antes da coleta, porque ela pode alterar glicose e outros valores do exame.",
     FONTE_SANGUE_DB),
    ("Exames de sangue com jejum (glicemia, perfil lipídico e outros)", "Comi sem querer durante o jejum, o que faço?",
     "Avise o laboratório antes da coleta. Dependendo do que foi ingerido e do exame, eles podem seguir com a coleta, anotar a informação ou reagendar.",
     FONTE_SANGUE_DB),
]


# Cargas por grupo de exames (2026-09-29), cada uma em seu módulo para manter
# este arquivo enxuto. Os módulos não importam nada do projeto.
from app.base_conhecimento_padrao_g1 import ITENS as _G1  # digestivo funcional e endoscópico
from app.base_conhecimento_padrao_g2 import ITENS as _G2  # ultrassom, tomografia e ressonância
from app.base_conhecimento_padrao_g3 import ITENS as _G3  # radiografia contrastada e medicina nuclear
from app.base_conhecimento_padrao_g4 import ITENS as _G4  # cardiologia
from app.base_conhecimento_padrao_g5 import ITENS as _G5  # laboratório
from app.base_conhecimento_padrao_g6 import ITENS as _G6  # ginecologia, urologia, pneumologia e outros

ITENS_PADRAO = ITENS_PADRAO + _G1 + _G2 + _G3 + _G4 + _G5 + _G6
# Ampliação (carga 5): agentes pesquisaram mais dúvidas por exame. Grupos 14 e 17 (sangue/urina e
# espirometria/polissonografia/EEG/retina/audiometria/nasofibro) ficaram sem ampliação por limite de uso.
from app.base_conhecimento_padrao_g7 import ITENS as _G7
from app.base_conhecimento_padrao_g8 import ITENS as _G8
from app.base_conhecimento_padrao_g9 import ITENS as _G9
from app.base_conhecimento_padrao_g10 import ITENS as _G10
from app.base_conhecimento_padrao_g11 import ITENS as _G11
from app.base_conhecimento_padrao_g12 import ITENS as _G12
from app.base_conhecimento_padrao_g13 import ITENS as _G13
from app.base_conhecimento_padrao_g15 import ITENS as _G15
from app.base_conhecimento_padrao_g16 import ITENS as _G16

ITENS_PADRAO = ITENS_PADRAO + _G7 + _G8 + _G9 + _G10 + _G11 + _G12 + _G13 + _G15 + _G16


def semear_base_conhecimento(db):
    """Insere os itens acima que ainda não existem (mesmo tipo de exame e
    mesma pergunta, sem diferenciar maiúsculas). Idempotente e NÃO
    destrutivo: nunca altera um item que o dono já editou. Precisa que os
    tipos de exame já tenham sido semeados (app.tipos_exame_padrao) - itens
    de um tipo que não existe são ignorados. Um commit só, no final.
    Devolve quantos itens novos foram criados."""
    from app.models import BaseConhecimentoItem, TipoExame

    tipos = {t.nome.strip().lower(): t for t in TipoExame.query.all()}
    existentes = {
        (i.tipo_exame_id, i.pergunta.strip().lower()) for i in BaseConhecimentoItem.query.all()
    }
    criados = 0
    for nome_tipo, pergunta, resposta, (fonte_nome, fonte_url) in ITENS_PADRAO:
        tipo = tipos.get(nome_tipo.strip().lower())
        if not tipo or (tipo.id, pergunta.strip().lower()) in existentes:
            continue
        db.session.add(BaseConhecimentoItem(
            tipo_exame_id=tipo.id, pergunta=pergunta, resposta=resposta,
            fonte_nome=fonte_nome, fonte_url=fonte_url,
            origem="internet", status="ativo", revisado=False, autor_nome="Carga inicial",
        ))
        existentes.add((tipo.id, pergunta.strip().lower()))
        criados += 1
    if criados:
        db.session.commit()
    return criados
