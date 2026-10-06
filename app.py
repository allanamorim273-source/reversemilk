"""Dashboard ReverseMilk — rode com:  streamlit run app.py"""
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from agendamento import BUFFER_MIN, agendamento_reverso, reprogramar
from gerar_dados import ROTEIROS, gerar
from indicadores import classificar_etapas, distribuicao_mc, resumo_ops

st.set_page_config(page_title="ReverseMilk", page_icon="🥛", layout="wide")

# impede o Chrome de traduzir (e estragar) a página
components.html("""<script>
const d = window.parent.document;
d.documentElement.setAttribute('translate', 'no'); d.documentElement.lang = 'pt-BR';
const m = d.createElement('meta'); m.name = 'google'; m.content = 'notranslate'; d.head.appendChild(m);
</script>""", height=0)

CORES = {"No prazo": "#2e9e5b", "Em risco": "#f0a30a", "Atrasada": "#d64545",
         "Concluída no prazo": "#1f6f43", "Concluída com atraso": "#8c5a2b"}
ICONES = {"No prazo": "🟢", "Em risco": "🟡", "Atrasada": "🔴",
          "Concluída no prazo": "✅", "Concluída com atraso": "🟤"}
COLS_DATA = ["prazo", "plan_inicio", "plan_fim", "real_inicio", "real_fim"]


# ---------------------------------------------------------------- dados
@st.cache_data
def dados_sinteticos(n, seed):
    return gerar(int(n), int(seed), base=datetime(2026, 10, 5, 6, 0))


@st.cache_data
def calcular(df, agora, tol, limite, n_sim):
    d = classificar_etapas(df, agora, tol)
    return d, resumo_ops(d, agora, limite, n_sim=n_sim)


st.sidebar.title("🥛 ReverseMilk")
arq = st.sidebar.file_uploader("Usar CSV próprio (opcional)", type="csv")
n_ops = st.sidebar.number_input("Nº de ordens (dados sintéticos)", 10, 200, 40, 5)
seed = st.sidebar.number_input("Semente aleatória", 0, 9999, 42)

df = pd.read_csv(arq, parse_dates=COLS_DATA) if arq else dados_sinteticos(n_ops, seed)

dmin = df.plan_inicio.min().floor("30min").to_pydatetime()
dmax = df.prazo.max().ceil("30min").to_pydatetime()
padrao = min(dmin + timedelta(hours=24), dmax)
agora = pd.Timestamp(st.sidebar.slider(
    "Horário atual (simulado)", min_value=dmin, max_value=dmax, value=padrao,
    step=timedelta(minutes=30), format="DD/MM HH:mm"))
tol = st.sidebar.slider("Tolerância de atraso por etapa (min)", 5, 120, 30)
limite = st.sidebar.slider("Margem mínima para alerta (min)", 15, 240, 30)
n_sim = st.sidebar.select_slider("Simulações Monte Carlo", [200, 500, 1000, 2000], 1000)

d, r = calcular(df, agora, tol, limite, n_sim)

st.title("ReverseMilk — Planejamento reverso e monitoramento da produção")
st.caption(f"Horário simulado: {agora:%d/%m/%Y %H:%M} · dados "
           f"{'do CSV enviado' if arq else 'sintéticos (simulação estocástica)'}")

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📊 Visão geral", "🗓️ Linha do tempo", "⏪ Agendamento reverso", "🧪 Simular atraso", "📁 Dados"])

# ------------------------------------------------------------ visão geral
with tab1:
    produtos = sorted(r.produto.unique())
    sel = st.multiselect("Filtrar por produto", produtos, default=produtos) or produtos
    r1 = r[r.produto.isin(sel)]
    d1 = d[d.produto.isin(sel)]
    em_and = r1[~r1.status.str.startswith("Concluída")]
    conc = r1[r1.status.str.startswith("Concluída")]
    impacto = (-em_and.margem_min).clip(lower=0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("OPs em andamento", len(em_and))
    c2.metric("Em risco", int((r1.status == "Em risco").sum()))
    c3.metric("Atrasadas (projeção)", int((r1.status == "Atrasada").sum()))
    c4.metric("Cumprimento da programação",
              f"{(conc.status == 'Concluída no prazo').mean() * 100:.0f}%" if len(conc) else "—")
    c5.metric("Impacto estimado médio", f"{impacto.mean():.0f} min" if len(em_and) else "—")

    st.subheader("🚨 Alertas preventivos")

    def mostrar_alerta(x):
        msg = (f"**{x.op}** ({x.produto}) · etapa: {x.etapa_atual} · projeção "
               f"{x.projecao_conclusao:%d/%m %H:%M} × prazo {x.prazo:%d/%m %H:%M} · "
               f"margem {x.margem_min:.0f} min · prob. de atraso {x.prob_atraso:.0%}")
        (st.error if x.status == "Atrasada" else st.warning)(msg)

    crit = em_and[em_and.status.isin(["Atrasada", "Em risco"])].sort_values("margem_min")
    if crit.empty:
        st.success("Nenhuma ordem em risco no momento.")
    for _, x in crit.head(5).iterrows():
        mostrar_alerta(x)
    if len(crit) > 5:
        with st.expander(f"Ver mais {len(crit) - 5} alertas"):
            for _, x in crit.iloc[5:].iterrows():
                mostrar_alerta(x)

    st.subheader("Ordens de produção")
    st_sel = st.multiselect("Filtrar por status", list(CORES), default=list(CORES)) or list(CORES)
    tab = r1[r1.status.isin(st_sel)].copy()
    tab["prob_atraso"] = tab.prob_atraso * 100
    tab["status"] = tab.status.map(lambda s: f"{ICONES[s]} {s}")
    tab["progresso"] = tab.etapas_concluidas.astype(str) + "/" + tab.total_etapas.astype(str)
    st.dataframe(
        tab[["op", "produto", "status", "etapa_atual", "progresso", "prazo",
             "projecao_conclusao", "p90_conclusao", "margem_min", "prob_atraso"]]
        .sort_values("margem_min"),
        hide_index=True, use_container_width=True,
        column_config={
            "op": "OP", "produto": "Produto", "status": "Status", "etapa_atual": "Etapa atual",
            "progresso": "Etapas", "prazo": st.column_config.DatetimeColumn("Prazo", format="DD/MM HH:mm"),
            "projecao_conclusao": st.column_config.DatetimeColumn("Projeção", format="DD/MM HH:mm"),
            "p90_conclusao": st.column_config.DatetimeColumn("Conclusão (P90)", format="DD/MM HH:mm"),
            "margem_min": st.column_config.NumberColumn("Margem (min)", format="%.0f"),
            "prob_atraso": st.column_config.ProgressColumn("Prob. de atraso", min_value=0,
                                                           max_value=100, format="%.0f%%"),
        })

    g1, g2, g3 = st.columns(3)
    cont = r1.status.value_counts().reset_index()
    cont.columns = ["status", "ordens"]
    g1.plotly_chart(px.bar(cont, x="status", y="ordens", color="status",
                           color_discrete_map=CORES, title="Ordens por status")
                    .update_layout(showlegend=False), use_container_width=True)

    iniciadas = d1[d1.situacao != "Pendente"].copy()
    iniciadas["gargalo"] = iniciadas.produto + " · " + iniciadas.etapa
    garg = (iniciadas.assign(atraso=iniciadas.atraso_min.clip(lower=0))
            .groupby("gargalo", as_index=False).atraso.mean()
            .sort_values("atraso", ascending=False).head(8))
    g2.plotly_chart(px.bar(garg, x="atraso", y="gargalo", orientation="h",
                           title="Gargalos: atraso médio por etapa (min)")
                    .update_yaxes(autorange="reversed", title=""), use_container_width=True)

    ocup = (d1[d1.situacao == "Em execução"].groupby("recurso").size()
            .reset_index(name="ordens_simultaneas"))
    g3.plotly_chart(px.bar(ocup, x="ordens_simultaneas", y="recurso", orientation="h",
                           title="Recursos ocupados agora").update_yaxes(title=""),
                    use_container_width=True)

    espera = d1.dropna(subset=["espera_min"]).groupby("etapa", as_index=False).espera_min.mean()
    st.plotly_chart(px.bar(espera.sort_values("espera_min", ascending=False),
                           x="etapa", y="espera_min",
                           title="Tempo médio de espera antes de cada etapa (min)"),
                    use_container_width=True)

# --------------------------------------------------------- linha do tempo
with tab2:
    op = st.selectbox("Ordem de produção", r.sort_values("margem_min").op)
    g = d[d.op == op].sort_values("ordem_etapa")
    prazo_op = g.prazo.iloc[0]
    plan = g.assign(Tipo="Planejado", ini=g.plan_inicio, fim=g.plan_fim)
    real = g[g.situacao != "Pendente"].assign(
        Tipo="Realizado", ini=lambda x: x.real_inicio, fim=lambda x: x.real_fim.clip(upper=agora))
    fig = px.timeline(pd.concat([plan, real]), x_start="ini", x_end="fim", y="etapa", color="Tipo",
                      color_discrete_map={"Planejado": "#9bb7d4", "Realizado": "#1f4e9c"},
                      category_orders={"etapa": list(g.etapa)})
    fig.update_yaxes(autorange="reversed", title="")
    fig.update_layout(barmode="group", height=480)
    for x, nome, cor in [(agora, "Agora", "#d64545"), (prazo_op, "Prazo", "#000000")]:
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, yref="paper",
                      line=dict(color=cor, dash="dash"))
        fig.add_annotation(x=x, y=1.05, yref="paper", text=nome, showarrow=False)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(g[["ordem_etapa", "etapa", "recurso", "situacao", "status_etapa", "plan_inicio",
                    "plan_fim", "real_inicio", "real_fim", "atraso_min", "desvio_ciclo_min",
                    "espera_min"]].round(1), hide_index=True, use_container_width=True)

    st.subheader("🎲 Monte Carlo: quando esta ordem deve terminar?")
    t0_mc, tot_mc = distribuicao_mc(d, agora, op)
    if tot_mc is None:
        st.info("Esta ordem já foi concluída — não há o que simular.")
    else:
        concl = t0_mc + pd.to_timedelta(tot_mc, unit="m")
        prob = float(np.mean(concl > prazo_op))
        p50 = t0_mc + pd.Timedelta(minutes=float(np.percentile(tot_mc, 50)))
        p90 = t0_mc + pd.Timedelta(minutes=float(np.percentile(tot_mc, 90)))
        k1, k2, k3 = st.columns(3)
        k1.metric("Prob. de atraso", f"{prob:.0%}")
        k2.metric("Conclusão mais provável (P50)", f"{p50:%d/%m %H:%M}")
        k3.metric("Cenário pessimista (P90)", f"{p90:%d/%m %H:%M}")
        h = px.histogram(x=pd.Series(concl), nbins=40, labels={"x": "Conclusão simulada"})
        h.add_shape(type="line", x0=prazo_op, x1=prazo_op, y0=0, y1=1, yref="paper",
                    line=dict(color="#d64545", dash="dash"))
        h.add_annotation(x=prazo_op, y=1.05, yref="paper", text="Prazo", showarrow=False)
        h.update_layout(yaxis_title="Simulações", showlegend=False)
        st.plotly_chart(h, use_container_width=True)

# ------------------------------------------------------ agendamento reverso
with tab3:
    st.markdown("Informe o **produto** e o **prazo de entrega**: o sistema calcula, de trás "
                "para frente, o horário-limite de cada etapa.")
    c1, c2, c3, c4 = st.columns(4)
    prod = c1.selectbox("Produto", list(ROTEIROS))
    dia = c2.date_input("Data de entrega", datetime(2026, 10, 6).date())
    hora = c3.time_input("Hora de entrega", time(18, 0))
    buf = c4.number_input("Folga entre etapas (min)", 0, 120, BUFFER_MIN)
    prazo = datetime.combine(dia, hora)
    plano = pd.DataFrame(agendamento_reverso(prazo, ROTEIROS[prod], buf))
    inicio_total = plano.plan_inicio.min()
    m1, m2 = st.columns(2)
    m1.metric("A produção deve começar até", f"{inicio_total:%d/%m %H:%M}")
    m2.metric("Duração total", f"{(prazo - inicio_total).total_seconds() / 3600:.1f} h")
    f = px.timeline(plano, x_start="plan_inicio", x_end="plan_fim", y="etapa", color="recurso",
                    category_orders={"etapa": list(plano.etapa)})
    f.update_yaxes(autorange="reversed", title="")
    st.plotly_chart(f, use_container_width=True)
    st.dataframe(plano[["ordem_etapa", "etapa", "recurso", "dur_padrao_min", "plan_inicio", "plan_fim"]],
                 hide_index=True, use_container_width=True)
    st.download_button("Baixar plano (CSV)", plano.to_csv(index=False).encode("utf-8"),
                       "plano_reverso.csv", "text/csv")

# ------------------------------------------------------------ simular atraso
with tab4:
    st.markdown("Cenário **“e se uma etapa atrasar?”** — veja quais etapas são afetadas e o "
                "impacto no prazo.")
    c1, c2, c3 = st.columns(3)
    op_s = c1.selectbox("Ordem", r.op, key="op_sim")
    plano_op = (df[df.op == op_s].sort_values("ordem_etapa"))
    etapa_s = c2.selectbox("Etapa que vai atrasar", list(plano_op.etapa))
    atraso = c3.slider("Atraso (min)", 0, 600, 120, 15)
    ordem_s = int(plano_op[plano_op.etapa == etapa_s].ordem_etapa.iloc[0])
    novo = reprogramar(plano_op, ordem_s, atraso)
    prazo_s = plano_op.prazo.iloc[0]
    estouro = (novo.novo_fim.iloc[-1] - prazo_s).total_seconds() / 60
    if estouro > 0:
        st.error(f"Conclusão passa para {novo.novo_fim.iloc[-1]:%d/%m %H:%M}: "
                 f"{estouro:.0f} min depois do prazo ({prazo_s:%d/%m %H:%M}).")
    else:
        st.success("O atraso não compromete o prazo de entrega.")
    afetadas = novo[novo.ordem_etapa > ordem_s]
    st.write("**Etapas subsequentes afetadas:** " +
             (", ".join(afetadas.etapa) if len(afetadas) else "nenhuma"))
    st.dataframe(novo[["etapa", "recurso", "plan_inicio", "novo_inicio", "plan_fim",
                       "novo_fim", "deslocamento_min"]].round(1),
                 hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ dados
with tab5:
    st.dataframe(d, hide_index=True, use_container_width=True)
    st.download_button("Baixar dados (CSV)", d.to_csv(index=False).encode("utf-8"),
                       "dados_reversemilk.csv", "text/csv")
