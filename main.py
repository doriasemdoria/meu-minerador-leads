import os
import subprocess
import asyncio
import re
import pandas as pd
import streamlit as st
import io
from playwright.async_api import async_playwright

# ============================================================
# 1. PREPARAÇÃO DO AMBIENTE (NUVEM)
# ============================================================
def preparar_navegador():
    """Instala apenas o executável do Chromium no servidor."""
    if 'navegador_pronto' not in st.session_state:
        try:
            # Comando simples: apenas baixa o binário do Chrome
            subprocess.run(["playwright", "install", "chromium"], check=True)
            st.session_state['navegador_pronto'] = True
        except Exception as e:
            st.error(f"Erro ao baixar executável do Chrome: {e}")

# Inicia a preparação assim que o site carrega
preparar_navegador()

# ============================================================
# 2. INTERFACE DO USUÁRIO (STREAMLIT)
# ============================================================
st.set_page_config(page_title="Lead Miner Pro", page_icon="🎯", layout="wide")

st.title("🎯 Lead Miner Pro - Google Maps")
st.markdown("Extraia contatos reais para audiências de tráfego pago ou prospecção direta.")

with st.sidebar:
    st.header("⚙️ Configurações")
    
    termos_raw = st.text_area("Termos (um por linha)", 
                             "Ginecologista\nObstetrícia")
    
    cidades_raw = st.text_area("Locais/Bairros (um por linha)", 
                              "Moema Sao Paulo\nIpiranga Sao Paulo")
    
    excluir_raw = st.text_area("Palavras para Excluir", 
                              "hospital\npublico\nuniversidade")
    
    st.divider()
    concorrencia = st.slider("Buscas Simultâneas", 1, 4, 1)
    max_rolagens = st.number_input("Rolagens de Página", 5, 100, 20)

# Tratamento dos inputs
TERMOS = [t.strip() for t in termos_raw.split('\n') if t.strip()]
CIDADES = [c.strip() for c in cidades_raw.split('\n') if c.strip()]
PALAVRAS_EXCLUIR = [p.strip().lower() for p in excluir_raw.split('\n') if p.strip()]

# ============================================================
# 3. NÚCLEO DO ROBÔ (SCRAPING)
# ============================================================
PADRAO_TEL = re.compile(r'\(?\d{2}\)?\s?9?\d{4,5}[\s-]?\d{4}')

async def bloquear_lixo(route):
    """Bloqueia imagens e vídeos para o site carregar mais rápido."""
    if route.request.resource_type in ("image", "font", "media"):
        await route.abort()
    else:
        await route.continue_()

async def extrair_contato(card, page):
    """Tenta pegar o telefone direto do card ou clicando nele."""
    try:
        # Tenta no card visível
        texto = await card.inner_text()
        tels = PADRAO_TEL.findall(texto)
        if tels: return tels[0]
        
        # Se não achou, clica no card
        await card.click()
        await asyncio.sleep(1.2) # Delay humano
        painel = await page.query_selector('[role="main"]')
        if painel:
            texto_painel = await painel.inner_text()
            tels = PADRAO_TEL.findall(texto_painel)
            if tels: return tels[0]
    except: pass
    return "Não listado"

async def executar_busca(context, setor, cidade, vistos, todos_leads, lock, status):
    busca = f"{setor} em {cidade}"
    status.write(f"🔎 Minerando: **{busca}**")
    
    page = await context.new_page()
    await page.route("**/*", bloquear_lixo)
    
    try:
        url = f"https://www.google.com/maps/search/{busca.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # Scroll lateral
        for _ in range(max_rolagens):
            feed = await page.query_selector('[role="feed"]')
            if feed:
                await page.evaluate("document.querySelector('[role=\"feed\"]').scrollTop += 1500")
                await asyncio.sleep(0.7)
            else: break
            
        cards = await page.query_selector_all('div[role="article"]')
        
        for card in cards:
            nome = await card.get_attribute("aria-label")
            if not nome or any(p in nome.lower() for p in PALAVRAS_EXCLUIR):
                continue
                
            tel = await extrair_contato(card, page)
            tel_limpo = re.sub(r'\D', '', tel)
            
            async with lock:
                # Evita duplicados por telefone ou nome
                chave = ("tel", tel_limpo) if len(tel_limpo) >= 10 else ("nome", nome.lower())
                if chave not in vistos:
                    vistos.add(chave)
                    todos_leads.append({
                        "Setor": setor,
                        "Nome": nome,
                        "Telefone": tel,
                        "WhatsApp": f"https://wa.me/55{tel_limpo}" if len(tel_limpo) >= 10 else "-",
                        "Bairro/Cidade": cidade
                    })
    finally:
        await page.close()

async def engine():
    vistos = set()
    todos_leads = []
    lock = asyncio.Lock()
    semaforo = asyncio.Semaphore(concorrencia)
    
    log_area = st.empty()
    progresso = st.progress(0)
    
    lista_tarefas = [(s, c) for s in TERMOS for c in CIDADES]
    total = len(lista_tarefas)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        
        tasks = []
        for i, (setor, cidade) in enumerate(lista_tarefas):
            async def wrapper(s, c, idx):
                async with semaforo:
                    await executar_busca(context, s, c, vistos, todos_leads, lock, log_area)
                    progresso.progress((idx + 1) / total)
            
            tasks.append(wrapper(setor, cidade, i))
            
        await asyncio.gather(*tasks)
        await browser.close()
        
    return todos_leads

# ============================================================
# 4. DISPARO E RESULTADOS
# ============================================================
if st.button("🚀 Iniciar Mineração"):
    if not TERMOS or not CIDADES:
        st.error("Preencha os termos e cidades na barra lateral!")
    else:
        with st.spinner("O robô está trabalhando..."):
            try:
                # Garante que o loop do asyncio funcione no Streamlit
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                resultado_final = loop.run_until_complete(engine())
                
                if resultado_final:
                    df = pd.DataFrame(resultado_final)
                    st.success(f"Sucesso! {len(df)} leads encontrados.")
                    st.dataframe(df, use_container_width=True)
                    
                    csv = df.to_csv(index=False, encoding='utf-8-sig')
                    st.download_button(
                        label="📥 Baixar Planilha CSV",
                        data=csv,
                        file_name="leads_extraidos.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("Nenhum lead encontrado com esses critérios.")
            except Exception as e:
                st.error(f"Erro durante a execução: {e}")
