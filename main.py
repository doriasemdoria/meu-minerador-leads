import os
import subprocess
import asyncio
import re
import pandas as pd
import streamlit as st
import io
from playwright.async_api import async_playwright

# --- INSTALAÇÃO ---
def preparar_navegador():
    if 'navegador_pronto' not in st.session_state:
        try:
            subprocess.run(["playwright", "install", "chromium"], check=True)
            st.session_state['navegador_pronto'] = True
        except: pass

preparar_navegador()

# --- INTERFACE ---
st.set_page_config(page_title="Lead Miner Pro", page_icon="🎯", layout="wide")
st.title("🎯 Lead Miner Pro - Ajuste de Performance")

with st.sidebar:
    st.header("⚙️ Configurações")
    termos_raw = st.text_area("Termos", "Ortopedista\nDentista")
    cidades_raw = st.text_area("Locais (Bairros)", "Moema Sao Paulo\nTatuape Sao Paulo")
    excluir_raw = st.text_area("Palavras para Excluir", "hospital\npublico")
    concorrencia = st.slider("Buscas Simultâneas", 1, 2, 1) 
    max_rolagens = st.number_input("Rolagens", 5, 100, 25)

TERMOS = [t.strip() for t in termos_raw.split('\n') if t.strip()]
CIDADES = [c.strip() for c in cidades_raw.split('\n') if c.strip()]
PALAVRAS_EXCLUIR = [p.strip().lower() for p in excluir_raw.split('\n') if p.strip()]

# --- MOTOR ---
PADRAO_TEL = re.compile(r'\(?\d{2}\)?\s?9?\d{4,5}[\s-]?\d{4}')

async def extrair_contato(card, page):
    try:
        texto = await card.inner_text()
        tels = PADRAO_TEL.findall(texto)
        if tels: return tels[0]
        
        await card.click()
        await asyncio.sleep(1.5) 
        painel = await page.query_selector('[role="main"]')
        if painel:
            texto_painel = await painel.inner_text()
            tels = PADRAO_TEL.findall(texto_painel)
            if tels: return tels[0]
    except: pass
    return "Não listado"

async def executar_busca(context, setor, cidade, vistos, todos_leads, lock, status):
    busca = f"{setor} em {cidade}"
    status.write(f"🔎 Minerando: **{busca}**...")
    page = await context.new_page()
    
    try:
        # URL direta
        url = f"https://www.google.com/maps/search/{busca.replace(' ', '+')}"
        
        # MUDANÇA AQUI: wait_until="domcontentloaded" é muito mais rápido
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # Espera um seletor específico (os cards) em vez da rede toda
        try:
            await page.wait_for_selector('div[role="article"]', timeout=15000)
        except:
            pass # Se não achar em 15s, tenta seguir assim mesmo

        # Lógica de Scroll mais "agressiva"
        for _ in range(max_rolagens):
            # Tenta focar no feed antes de rolar
            feed = await page.query_selector('[role="feed"]')
            if feed:
                await feed.focus()
                await page.mouse.wheel(0, 3000)
            else:
                await page.mouse.wheel(0, 3000)
            await asyncio.sleep(0.6)
            
        cards = await page.query_selector_all('div[role="article"]')
        
        for card in cards:
            try:
                nome = await card.get_attribute("aria-label")
                if not nome:
                    txt = await card.inner_text()
                    nome = txt.split('\n')[0]

                if not nome or any(p in nome.lower() for p in PALAVRAS_EXCLUIR):
                    continue
                
                tel_bruto = await extrair_contato(card, page)
                tel_limpo = re.sub(r'\D', '', tel_bruto)
                
                async with lock:
                    chave = ("tel", tel_limpo) if len(tel_limpo) >= 10 else ("nome", nome.lower())
                    if chave not in vistos:
                        vistos.add(chave)
                        todos_leads.append({
                            "Nome": nome[:60],
                            "Telefone_API": f"55{tel_limpo}" if len(tel_limpo) >= 10 else "Incompleto",
                            "Setor": setor,
                            "Cidade": cidade
                        })
            except: continue
    finally:
        await page.close()

async def engine():
    vistos = set()
    todos_leads = []
    lock = asyncio.Lock()
    semaforo = asyncio.Semaphore(concorrencia)
    log_area = st.empty()
    progresso = st.progress(0)
    buscas = [(s, c) for s in TERMOS for c in CIDADES]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        for i, (setor, cidade) in enumerate(buscas):
            async with semaforo:
                await executar_busca(context, setor, cidade, vistos, todos_leads, lock, log_area)
                progresso.progress((i + 1) / len(buscas))
        
        await browser.close()
    return todos_leads

if st.button("🚀 Iniciar Mineração"):
    with st.spinner("Extraindo..."):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            res = loop.run_until_complete(engine())
            if res:
                df = pd.DataFrame(res)
                st.success(f"Encontrados {len(df)} leads!")
                st.dataframe(df)
                st.download_button("📥 Baixar CSV", df.to_csv(index=False, encoding='utf-8-sig'), "leads.csv")
            else:
                st.warning("Nenhum lead encontrado. Tente mudar o bairro ou remover palavras de exclusão.")
        except Exception as e:
            st.error(f"Erro: {e}")
