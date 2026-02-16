import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 페이지 설정
st.set_page_config(layout="wide", page_title="주식 백테스팅 & 검색기")

# --- 유틸리티 함수 ---

def get_stock_list(market_type=None, uploaded_file=None):
    """KOSPI/KOSDAQ 종목 리스트를 가져오거나 업로드된 CSV를 읽습니다."""
    # 1. 사용자 업로드 파일 우선
    if uploaded_file is not None:
        try:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, dtype={'Code': str})
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, dtype={'Code': str}, encoding='cp949')
        except Exception as e:
            st.error(f"파일 읽기 오류: {e}")
            return pd.DataFrame()

        # 필수 컬럼 확인
        if 'Code' not in df.columns or 'Name' not in df.columns:
            st.error("CSV 파일에 'Code'와 'Name' 컬럼이 있어야 합니다.")
            return pd.DataFrame()
        return df[['Code', 'Name']]

    # 2. 시장 데이터 가져오기 (업로드 파일이 없을 때만 실행)
    try:
        if market_type == "KOSPI":
            df = fdr.StockListing('KOSPI')
        elif market_type == "KOSDAQ":
            df = fdr.StockListing('KOSDAQ')
        else:
            df_kospi = fdr.StockListing('KOSPI')
            df_kosdaq = fdr.StockListing('KOSDAQ')
            df_total = pd.concat([df_kospi, df_kosdaq])
            return df_total[['Code', 'Name']]
            
        return df[['Code', 'Name']]
    except Exception as e:
        # Fallback to local CSV if available
        try:
            return pd.read_csv('kospi_stocks.csv', dtype={'Code': str})
        except:
             # Hardcoded fallback as last resort
            data = {
                'Code': ['005930', '000660', '035420', '035720', '005380'],
                'Name': ['삼성전자', 'SK하이닉스', 'NAVER', '카카오', '현대차']
            }
            return pd.DataFrame(data)

def calculate_mas(df):
    """이동평균선을 계산합니다."""
    df['MA5'] = df['Close'].rolling(window=5).mean()
    df['MA20'] = df['Close'].rolling(window=20).mean()
    df['MA60'] = df['Close'].rolling(window=60).mean()
    df['MA120'] = df['Close'].rolling(window=120).mean()
    return df

def check_conditions(df, selected_conditions):
    """선택된 조건들을 모두 만족하는 시점을 찾아 Boolean Series로 반환합니다."""
    # 데이터가 충분하지 않으면 False 반환
    if len(df) < 120:
        return pd.Series([False] * len(df), index=df.index)

    # 기본 마스크 (모두 True로 시작 -> AND 연산)
    combined_mask = pd.Series([True] * len(df), index=df.index)
    
    # 1. 이평선 정배열
    if "정배열" in selected_conditions:
        mask = (df['MA20'] > df['MA60']) & (df['MA60'] > df['MA120'])
        combined_mask = combined_mask & mask

    # 2. 골든크로스 (20 > 60)
    if "MA 20>60" in selected_conditions:
        mask = (df['MA20'].shift(1) < df['MA60'].shift(1)) & (df['MA20'] > df['MA60'])
        combined_mask = combined_mask & mask

    # 3. 골든크로스 (20 > 120)
    if "MA 20>120" in selected_conditions:
        mask = (df['MA20'].shift(1) < df['MA120'].shift(1)) & (df['MA20'] > df['MA120'])
        combined_mask = combined_mask & mask
        
    # 4. 주가 골든크로스 (종가 > 20)
    if "종가 > 20선" in selected_conditions:
        mask = (df['Close'].shift(1) < df['MA20'].shift(1)) & (df['Close'] > df['MA20'])
        combined_mask = combined_mask & mask

    # 5. 주가 골든크로스 (종가 > 60)
    if "종가 > 60선" in selected_conditions:
        mask = (df['Close'].shift(1) < df['MA60'].shift(1)) & (df['Close'] > df['MA60'])
        combined_mask = combined_mask & mask
        
    # 6. 주가 골든크로스 (종가 > 120)
    if "종가 > 120선" in selected_conditions:
        mask = (df['Close'].shift(1) < df['MA120'].shift(1)) & (df['Close'] > df['MA120'])
        combined_mask = combined_mask & mask
        
    # 7. 거래량 급증 (전일 대비 +100% 이상, 즉 2배)
    if "거래량 +100%" in selected_conditions:
        mask = df['Volume'] >= df['Volume'].shift(1) * 2
        combined_mask = combined_mask & mask

    # 8. 거래량 급증 (전일 대비 +200% 이상, 즉 3배)
    if "거래량 +200%" in selected_conditions:
        mask = df['Volume'] >= df['Volume'].shift(1) * 3
        combined_mask = combined_mask & mask
        
    # 9. 거래량 급증 (전일 대비 +300% 이상, 즉 4배)
    if "거래량 +300%" in selected_conditions:
        mask = df['Volume'] >= df['Volume'].shift(1) * 4
        combined_mask = combined_mask & mask

    return combined_mask

def backtest_single_stock(code, name, start_date, end_date, condition, n_days):
    """단일 종목에 대해 백테스팅을 수행합니다."""
    # 데이터 로드 (이평선 계산을 위해 앞부분 데이터 여유있게 로드)
    fetch_start = start_date - datetime.timedelta(days=200) 
    df = fdr.DataReader(code, fetch_start, end_date)
    
    if df.empty:
        return None, None

    df = calculate_mas(df)
    
    # 조건 만족 여부 체크
    df['Signal'] = check_conditions(df, condition)
    
    # 검색 기간 내의 데이터만 필터링
    mask_period = (df.index >= pd.to_datetime(start_date)) & (df.index <= pd.to_datetime(end_date))
    target_df = df.loc[mask_period & df['Signal']].copy()
    
    results = []
    
    for date in target_df.index:
        # 매수일 종가
        entry_price = target_df.loc[date, 'Close']
        
        # N일 후 날짜 찾기 (거래일 기준)
        # 전체 df에서 현재 날짜의 정수 인덱스를 찾고 N을 더함
        try:
            current_idx = df.index.get_loc(date)
            future_idx = current_idx + n_days
            
            if future_idx < len(df):
                exit_date = df.index[future_idx]
                exit_price = df.iloc[future_idx]['Close']
                
                pct_change = (exit_price - entry_price) / entry_price * 100
                result = "상승" if pct_change > 0 else "하락"
                
                results.append({
                    '종목명': name,
                    '매수일': date.strftime('%Y-%m-%d'),
                    '매수가': entry_price,
                    f'{n_days}일후 날짜': exit_date.strftime('%Y-%m-%d'),
                    f'{n_days}일후 가격': exit_price,
                    '수익률(%)': round(pct_change, 2),
                    '결과': result
                })
        except Exception:
            continue
            
    return pd.DataFrame(results), df

# --- UI 구성 ---

st.title("📈 주식 전략 백테스팅 & 검색기")
st.markdown("---")

# 1. 사이드바 설정
with st.sidebar:
    st.header("🔍 검색 및 설정")
    
    market_select = st.radio("시장 선택", ["KOSPI", "KOSDAQ", "전체"])
    uploaded_file = st.file_uploader("나만의 종목 리스트 업로드 (CSV)", type=['csv'])

    st.subheader("기간 설정")
    start_date = st.date_input("시작일", datetime.date.today() - datetime.timedelta(days=365))
    end_date = st.date_input("종료일", datetime.date.today())
    
    st.subheader("전략 조건")
    st.subheader("전략 조건 (다중 선택 가능)")
    condition_select = st.multiselect(
        "검색 조건 선택 (AND 조건)",
        [
            "정배열",
            "MA 20>60",
            "MA 20>120",
            "종가 > 20선",
            "종가 > 60선",
            "종가 > 120선",
            "거래량 +100%",
            "거래량 +200%",
            "거래량 +300%"
        ],
        default=["정배열"]
    )
    
    n_days = st.number_input("N일 후 수익률 확인", min_value=1, max_value=100, value=5)

# 2. 메인 기능 탭
tab1, tab2 = st.tabs(["📊 단일 종목 상세 백테스트", "🔎 전체 종목 스캐닝"])

# --- 탭 1: 단일 종목 백테스트 ---
with tab1:
    st.markdown("### 특정 종목을 선택하여 전략을 검증합니다.")
    
    stock_list = get_stock_list(market_select, uploaded_file)
    # 검색 편의를 위해 "종목명 (코드)" 형식으로 리스트 생성
    stock_choices = stock_list.apply(lambda x: f"{x['Name']} ({x['Code']})", axis=1)
    selected_stock_str = st.selectbox("종목 검색", stock_choices)
    
    if st.button("백테스팅 시작", key='single_btn'):
        name = selected_stock_str.split(' (')[0]
        code = selected_stock_str.split(' (')[1][:-1]
        
        with st.spinner(f'{name} 데이터를 분석 중입니다...'):
            result_df, df = backtest_single_stock(code, name, start_date, end_date, condition_select, n_days)
            
            if result_df is not None and not result_df.empty:
                st.success("분석 완료!")
                
                # 요약 통계
                total_trades = len(result_df)
                win_trades = len(result_df[result_df['수익률(%)'] > 0])
                win_rate = (win_trades / total_trades) * 100
                avg_return = result_df['수익률(%)'].mean()
                
                col1, col2, col3 = st.columns(3)
                col1.metric("총 매매 횟수", f"{total_trades}회")
                col2.metric("승률 (수익 마감)", f"{win_rate:.2f}%")
                col3.metric(f"평균 수익률 ({n_days}일 후)", f"{avg_return:.2f}%")
                
                st.dataframe(result_df, use_container_width=True)
                
                # 차트 시각화 (Plotly Candlestick + Volume)
                st.subheader(f"📊 {name} ({code}) 주가 차트")
                
                # mask_period 재계산 필요 (함수 내부 로직과 동일하게)
                mask_period = (df.index >= pd.to_datetime(start_date)) & (df.index <= pd.to_datetime(end_date))
                chart_df = df.loc[mask_period].copy()
                
                # Subplots 생성 (2행 1열, 높이 비율 조절)
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                                    vertical_spacing=0.03, 
                                    row_heights=[0.7, 0.3],
                                    subplot_titles=(f'{name} 주가', '거래량'))
                
                # 거래량 색상 계산 (전체 df 기준)
                df['VolColor'] = ['red' if df['Volume'].iloc[i] >= df['Volume'].iloc[i-1] else 'blue' for i in range(len(df))]
                df.iloc[0, df.columns.get_loc('VolColor')] = 'red' 

                # 차트 데이터 추출
                chart_df = df.loc[mask_period].copy()
                
                # 주말/공휴일 제거를 위해 x축을 문자열로 변환 (Category type) - 모든 트레이스에 적용됨
                chart_df.index = chart_df.index.strftime('%Y-%m-%d')

                # 1. 캔들차트 (Row 1)
                fig.add_trace(go.Candlestick(x=chart_df.index,
                                open=chart_df['Open'],
                                high=chart_df['High'],
                                low=chart_df['Low'],
                                close=chart_df['Close'],
                                increasing_line_color='red',
                                decreasing_line_color='blue',
                                name='Price'), row=1, col=1)
                
                # 2. 이동평균선 (Row 1)
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['MA5'], line=dict(color='purple', width=1), name='MA5'), row=1, col=1)
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['MA20'], line=dict(color='orange', width=1), name='MA20'), row=1, col=1)
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['MA60'], line=dict(color='green', width=1), name='MA60'), row=1, col=1)
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['MA120'], line=dict(color='gray', width=1), name='MA120'), row=1, col=1)
                
                # 3. 거래량 (Row 2)
                fig.add_trace(go.Bar(x=chart_df.index, y=chart_df['Volume'], marker_color=chart_df['VolColor'], name='Volume'), row=2, col=1)
                
                # x축 설정: type='category'로 설정하여 빈 날짜(주말 등) 제거
                fig.update_xaxes(type='category', row=1, col=1)
                fig.update_xaxes(type='category', row=2, col=1)
                
                # 틱 라벨이 너무 많아지는 것을 방지 (적절히 건너뛰기)
                # category type에서는 nticks가 잘 안 먹힐 수 있음. tickmode='auto' 유지.
                
                fig.update_layout(xaxis_rangeslider_visible=False, height=600)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("설정된 기간 내에 조건에 부합하는 신호가 없습니다.")

# --- 탭 2: 전체 종목 스캐닝 ---
with tab2:
    st.markdown("### 전체 시장에서 조건에 맞는 종목을 찾습니다.")
    st.info("⚠️ 전체 종목 검색은 시간이 오래 걸릴 수 있어, 시가총액 상위 종목으로 제한하거나 샘플링하는 것을 권장합니다.")
    
    limit_num = st.slider("검색 대상 종목 수 (시가총액 상위 순)", 10, 200, 50)
    
    if st.button("조건 만족 종목 추출", key='scan_btn'):
        stock_list = get_stock_list(market_select, uploaded_file)
        # 상위 N개만 테스트 (속도 문제 해결)
        target_stocks = stock_list.head(limit_num)
        
        final_results = []
        progress_bar = st.progress(0)
        
        status_text = st.empty()
        
        for idx, row in target_stocks.iterrows():
            # 진행률 표시
            progress = (idx + 1) / len(target_stocks)
            progress_bar.progress(progress)
            status_text.text(f"분석 중: {row['Name']} ({idx+1}/{limit_num})")
            
            # 개별 종목 백테스트 실행 (df는 스캔에서 불필요)
            res, _ = backtest_single_stock(row['Code'], row['Name'], start_date, end_date, condition_select, n_days)
            
            if res is not None and not res.empty:
                # 해당 종목의 평균 성과를 요약해서 저장
                avg_ret = res['수익률(%)'].mean()
                win_cnt = len(res[res['수익률(%)'] > 0])
                win_rt = (win_cnt / len(res)) * 100
                count = len(res)
                
                final_results.append({
                    '종목명': row['Name'],
                    '종목코드': row['Code'],
                    '발생 횟수': count,
                    '평균 수익률(%)': round(avg_ret, 2),
                    '승률(%)': round(win_rt, 2)
                })
        
        status_text.text("검색 완료!")
        progress_bar.empty()
        
        if final_results:
            result_summary = pd.DataFrame(final_results)
            # 평균 수익률 순으로 정렬
            result_summary = result_summary.sort_values(by='평균 수익률(%)', ascending=False)
            
            # 승률 70% 이상 필터링
            filtered_summary = result_summary[result_summary['승률(%)'] >= 70.0]
            
            st.write(f"검색 결과: 총 {len(filtered_summary)}개 종목 발견 (승률 70% 이상)")
            st.dataframe(filtered_summary, use_container_width=True)
        else:
            st.warning("조건을 만족하는 종목을 찾지 못했습니다.")