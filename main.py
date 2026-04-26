import os
import subprocess
import asyncio
import re
import pandas as pd
import streamlit as st
import io
from playwright.async_api import async_playwright

# ============================================================
# 1. INSTALAÇÃO AUTOMÁTICA (ESSENCIAL PARA NUVEM)
# ============================================================
def preparar_navegador():
    """Garante que o Chromium e as dependências estejam instalados no servidor."""
    if 'navegador_pronto' not in st.session_state:
        try:
            # Tenta instalar o chromium
            subprocess.run(["playwright", "install", "chromium"], check=True)
            # Instala dependências de sistema (linux)
            subprocess.run(["playwright", "install-deps"], check=True)
            st.session_state['navegador_pronto'] = True
        except Exception as e:
            st.error(f"Erro ao preparar navegador: {e}")

# Executa a preparação assim que o app abre
preparar_navegador()

# ============================================================
# 2. CONFIGURAÇÕES DA INTERFACE
# ============================================================
st.set_page_config(page_title="Lead Miner Pro", page_icon="🎯", layout="wide")

st.title("🎯 Lead Miner Pro - Google Maps")
st.markdown("Gere listas de leads qualificadas para campanhas de tráfego pago.")

with st.sidebar:
    st.header("⚙️ Configurações de Busca")
    
    termos_raw = st.text_area("Termos de Pesquisa (um por linha)", 
                             "Ginecologista\nObstetrícia\nClínica de Ginecologia")
    
    cidades_raw = st.text_area("Cidades/Bairros (um por linha)", 
                              "Moema Sao Paulo\nIpiranga Sao Paulo")
    
    excluir_raw = st.text_area("Palavras para Excluir do Nome", 
                              "hospital\npublico\nuniversidade")
    
    st.divider()
    concorrencia = st.slider("Buscas Simultâneas (Abas)", 1, 4, 1)
    max_rolagens = st.number_input("Máximo de Rolagens por Busca", 5, 100, 20)

# Processamento das listas de entrada
TERMOS = [t.strip() for t in termos_raw.split('\n') if t.strip()]
CIDADES = [c.strip() for c in cidades_raw.split('\n') if c.strip()]
PALAVRAS_EXCLUIR = [p.strip().lower() for p in excluir_raw.split('\n') if p.strip()]

# ============================================================
# 3. LÓGICA DE MINERAÇÃO (ASYNC)
# ============================================================
PADRAO_TEL = re.compile(r'\(?\d{2}\)?\s?9?\d{4,5}[\s-]?\d{4}')

async def bloquear_recursos(route):
    """Bloqueia imagens e mídia para economizar banda e memória do servidor."""
    if route.request.resource_type in ("image", "font", "media"):
        await route.abort()
    else:
        await route.continue_()

async def extrair_telefone(card, page):
    """Tenta extrair o telefone do card ou abrindo os detalhes."""
    try:
        # Tenta no card primeiro
        texto = await card.inner_text()
        tels = PADRAO_TEL.findall(texto)
        if tels: return tels[0]
        
        # Clica para abrir detalhes
        await card.click()
        await asyncio.sleep(1.5)
        detalhes = await page.query_selector('[role="main"]')
        if detalhes:
            texto_detalhes = await detalhes.inner_text()
            tels = PADRAO_TEL.findall(texto_detalhes)
            if tels: return tels[0]
    except: pass
    return "Não listado"

async def processar_cidade(context, setor, cidade, vistos, todos_leads, lock, status_placeholder):
    busca = f"{setor} em {cidade}"
    status_placeholder.write(f"🔎 Processando: **{busca}**")
    
    page = await context.new_page()
    await page.route("**/*", bloquear_recursos)
    
    try:
        url = f"https://www.google.com/maps/search/{busca.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # Scroll para carregar resultados
        for _ in range(max_rolagens):
            feed = await page.query_selector('[role="feed"]')
            if feed:
                await page.evaluate("document.querySelector('[role=\"feed\"]').scrollTop += 2000")
                await asyncio.sleep(0.8)
            else: break
            
        cards = await page.query_selector_all('div[role="article"]')
        
        for card in cards:
            nome = await card.get_attribute("aria-label")
            if not nome: continue
            
            # Filtro de exclusão
            if any(p in nome.lower() for p in PALAVRAS_EXCLUIR):
                continue
                
            tel = await extrair_telefone(card, page)
            tel_limpo = re.sub(r'\D', '', tel)
            
            async with lock:
                chave = ("tel", tel_limpo) if len(tel_limpo) >= 10 else ("nome", nome.lower())
                if chave not in vistos:
                    vistos.add(chave)
                    todos_leads.append({
                        "Setor": setor,
                        "Nome": nome,
                        "Telefone": tel,
                        "WhatsApp_Link": f"https://wa.me/55{tel_limpo}" if len(tel_limpo) >= 10 else "N/A",
                        "Cidade/Bairro": cidade
                    })
    finally:
        await page.close()

async def minerar_tudo():
    vistos = set()
    todos_leads = []
    lock = asyncio.Lock()
    semaforo = asyncio.Semaphore(concorrencia)
    
    container_status = st.empty()
    progresso = st.progress(0)
    
    buscas = [(s, c) for s in TERMOS for c in CIDADES]
    total = len(buscas)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) # Headless obrigatório em nuvem
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        
        tasks = []
        for i, (setor, cidade) in enumerate(buscas):
            async def wrapper(s, c, idx):
                async with semaforo:
                    await processar_cidade(context, s, c, vistos, todos_leads, lock, container_status)
                    progresso.progress((idx + 1) / total)
            
            tasks.append(wrapper(setor, cidade, i))
            
        await asyncio.gather(*tasks)
        await browser.close()
        
    return todos_leads

# ============================================================
# 4. EXECUÇÃO E DOWNLOAD
# ============================================================
if st.button("🚀 Iniciar Mineração"):
    if not TERMOS or not CIDADES:
        st.warning("Por favor, preencha pelo menos um termo e uma cidade.")
    else:
        with st.spinner("Minerando... Isso pode levar alguns minutos."):
            try:
                # Resolve o problema de loop de eventos do asyncio no Streamlit
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                resultados = loop.run_until_complete(minerar_tudo())
                
                if resultados:
                    df = pd.DataFrame(resultados)
                    st.success(f"Busca finalizada! {len(df)} leads capturados.")
                    st.dataframe(df)
                    
                    # Botão de download
                    csv_io = io.StringIO()
                    df.to_csv(csv_io, index=False, encoding='utf-8-sig')
                    st.download_button(
                        label="📥 Baixar Lista para Tráfego Pago",
                        data=csv_io.getvalue(),
                        file_name="leads_google_maps.csv",
                        mime="text/csv"
                    )
                else:
                    st.error("Nenhum resultado encontrado. Tente termos menos restritivos.")
            except Exception as e:
                st.error(f"Ocorreu um erro crítico: {e}")
