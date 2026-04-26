import streamlit as st
import asyncio
import csv
import re
import pandas as pd
from playwright.async_api import async_playwright
import io

# --- CONFIGURAÇÕES DE UI ---
st.set_page_config(page_title="Lead Miner Pro", page_icon="🎯")
st.title("🎯 Lead Miner: Google Maps")
st.markdown("Extraia leads qualificados diretamente para o seu tráfego pago.")

# --- SIDEBAR: CONFIGURAÇÕES ---
with st.sidebar:
    st.header("Configurações")
    termos_input = st.text_area("Termos de Pesquisa (um por linha)", "Ginecologista\nObstetrícia")
    cidades_input = st.text_area("Cidades/Bairros (um por linha)", "Moema Sao Paulo\nIpiranga Sao Paulo")
    excluir_input = st.text_area("Palavras para Excluir (filtro)", "clinica\nhospital")
    
    concorrencia = st.slider("Buscas Simultâneas", 1, 4, 2)
    max_rolagens = st.number_input("Máximo de Rolagens", 10, 100, 20)

# --- LÓGICA DE EXTRAÇÃO (ADAPTADA) ---
PADRAO_TEL = re.compile(r'\(?\d{2}\)?\s?9?\d{4,5}[\s-]?\d{4}')

async def extrair_telefone_do_card(card, page):
    try:
        texto_bruto = await card.inner_text()
        tels = PADRAO_TEL.findall(texto_bruto)
        if tels: return tels[0]
        
        await card.click()
        await asyncio.sleep(1) # Espera carregar painel
        detalhes = await page.query_selector('[role="main"]')
        if detalhes:
            texto_detalhes = await detalhes.inner_text()
            tels = PADRAO_TEL.findall(texto_detalhes)
            if tels: return tels[0]
    except: pass
    return "Nao listado"

async def processar_busca(context, setor, cidade, vistos, todos_leads, lock, log_placeholder, progress_bar, atual, total_buscas):
    busca = f"{setor} em {cidade}"
    log_placeholder.text(f"🔎 Buscando: {busca} ({atual}/{total_buscas})")
    
    page = await context.new_page()
    try:
        url = f"https://www.google.com/maps/search/{busca.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # Simulação de rolagem simples para o Streamlit
        for _ in range(max_rolagens):
            await page.evaluate("document.querySelector('[role=\"feed\"]').scrollTop += 2000")
            await asyncio.sleep(0.5)
            
        cards = await page.query_selector_all('div[role="article"]')
        
        for card in cards:
            nome = await card.get_attribute("aria-label")
            if not nome or any(p.lower() in nome.lower() for p in excluir_input.split('\n')):
                continue
                
            telefone = await extrair_telefone_do_card(card, page)
            tel_limpo = re.sub(r'\D', '', telefone)
            
            async with lock:
                chave = ("tel", tel_limpo) if len(tel_limpo) > 5 else ("nome", nome.lower())
                if chave not in vistos:
                    vistos.add(chave)
                    todos_leads.append({
                        "Setor": setor, "Nome": nome, "Telefone": telefone, "Cidade": cidade
                    })
    finally:
        await page.close()

async def iniciar_mineracao():
    termos = [t.strip() for t in termos_input.split('\n') if t.strip()]
    locais = [c.strip() for c in cidades_input.split('\n') if c.strip()]
    todas_buscas = [(t, l) for t in termos for l in locais]
    
    total = len(todas_buscas)
    vistos = set()
    todos_leads = []
    lock = asyncio.Lock()
    semaforo = asyncio.Semaphore(concorrencia)
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    log_container = st.expander("Logs do Processo", expanded=True)

    async with async_playwright() as p:
        # IMPORTANTE: headless=True para rodar no servidor
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0...")
        
        tasks = []
        for i, (setor, cidade) in enumerate(todas_buscas):
            async def task_wrapper(s, c, idx):
                async with semaforo:
                    await processar_busca(context, s, c, vistos, todos_leads, lock, status_text, progress_bar, idx+1, total)
                    progress_bar.progress((idx + 1) / total)

            tasks.append(task_wrapper(setor, cidade, i))
        
        await asyncio.gather(*tasks)
        await browser.close()
        return todos_leads

# --- BOTÃO DE AÇÃO ---
if st.button("🚀 Iniciar Mineração"):
    with st.spinner("O robô está trabalhando..."):
        resultados = asyncio.run(iniciar_mineracao())
        
        if resultados:
            df = pd.DataFrame(resultados)
            st.success(f"Finalizado! {len(df)} leads únicos encontrados.")
            st.dataframe(df)
            
            # Botão de Download
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
            st.download_button(
                label="📥 Baixar CSV para Tráfego Pago",
                data=csv_buffer.getvalue(),
                file_name="leads_extraidos.csv",
                mime="text/csv"
            )
        else:
            st.warning("Nenhum lead encontrado com esses critérios.")