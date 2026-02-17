import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 페이지 설정
st.set_page_config(layout="wide", page_title="주식 백테스팅 & 검색기")

# --- 유틸리티 함수 ---

@st.cache_data
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

def calculate_mas(df, periods=[5, 20, 60, 120]):
    """이동평균선을 계산합니다. periods 리스트에 있는 기간들을 계산합니다."""
    for p in periods:
        df[f'MA{p}'] = df['Close'].rolling(window=p).mean()
    return df

def check_conditions(df, params):
    """선택된 조건들을 모두 만족하는 시점을 찾아 Boolean Series로 반환합니다."""
    # 데이터가 충분하지 않으면 False 반환
    if len(df) < 120:
        return pd.Series([False] * len(df), index=df.index)

    # 기본 마스크 (모두 True로 시작 -> AND 연산)
    combined_mask = pd.Series([True] * len(df), index=df.index)
    
    # 1. 이평선(일)
    if 'ma' in params:
        p = params['ma']
        # 예: MA20 > MA60 > MA120
        mask = (df[f'MA{p["ma1"]}'] > df[f'MA{p["ma2"]}']) & (df[f'MA{p["ma2"]}'] > df[f'MA{p["ma3"]}'])
        combined_mask = combined_mask & mask

    # 2. 주가 돌파(일)
    if 'breakout' in params:
        p = params['breakout']
        # 시가/종가 컬럼 매핑
        col_map = {'시가': 'Open', '종가': 'Close'}
        price_col = df[col_map[p['price_type']]]
        ma_col = df[f'MA{p["target_ma"]}']
        
        if p['operator'] == '>':
            mask = price_col > ma_col
        else: # '<'
            mask = price_col < ma_col
        combined_mask = combined_mask & mask

    # 3. 주가 등락(일)
    if 'change' in params:
        p = params['change']
        # 등락률 계산: (오늘 종가 - 어제 종가) / 어제 종가 * 100
        daily_ret = df['Close'].pct_change() * 100
        
        r_min, r_max = 0, float('inf')
        if p['range'] == '3~5': r_min, r_max = 3, 5
        elif p['range'] == '5~7': r_min, r_max = 5, 7
        elif p['range'] == '7~9': r_min, r_max = 7, 9
        elif p['range'] == '9이상': r_min = 9

        if p['direction'] == '상승':
            mask = (daily_ret >= r_min) & (daily_ret < r_max)
        else: # 하락 (절대값 비교)
            mask = (daily_ret <= -r_min) & (daily_ret > -r_max)
        combined_mask = combined_mask & mask

    # 4. 거래량(일)
    if 'volume' in params:
        p = params['volume']
        # 거래량 변화율
        vol_change = df['Volume'].pct_change() * 100
        
        v_min, v_max = 0, float('inf')
        if p['range'] == '100~200': v_min, v_max = 100, 200
        elif p['range'] == '200~300': v_min, v_max = 200, 300
        elif p['range'] == '300이상': v_min = 300
        
        if p['direction'] == '상승':
            mask = (vol_change >= v_min) & (vol_change < v_max)
        else: # 하락
            mask = (vol_change <= -v_min) & (vol_change > -v_max)
        combined_mask = combined_mask & mask

    return combined_mask

def backtest_single_stock(code, name, start_date, end_date, condition, n_days):
    """단일 종목에 대해 백테스팅을 수행합니다."""
    # 데이터 로드 (이평선 계산을 위해 앞부분 데이터 여유있게 로드)
    fetch_start = start_date - datetime.timedelta(days=200) 
    df = fdr.DataReader(code, fetch_start, end_date)
    
    if df.empty:
        return None, None

    # 필요한 이평선 기간 추출
    ma_periods = {5, 20, 60, 120} # 기본 차트용
    
    if 'ma' in condition:
        ma_periods.add(condition['ma']['ma1'])
        ma_periods.add(condition['ma']['ma2'])
        ma_periods.add(condition['ma']['ma3'])
    
    if 'ma_cross' in condition:
        ma_periods.add(condition['ma_cross']['ma1'])
        ma_periods.add(condition['ma_cross']['ma2'])
    
    if 'breakout' in condition:
        ma_periods.add(condition['breakout']['target_ma'])
        
    df = calculate_mas(df, periods=list(ma_periods))
    
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
            
            # 입력한 수치가 매매 후 가장 최근 날짜까지의 일수보다도 높다면, 자동으로 가장 최근 날짜까지만 계산
            if future_idx >= len(df):
                future_idx = len(df) - 1
            
            # 미래 시점의 데이터가 현재보다 뒤에 있는 경우에만 계산
            if future_idx > current_idx:
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

def render_ma_input(label, default_val, key):
    """드롭다운과 숫자 입력을 결합한 UI를 렌더링합니다."""
    options = [5, 20, 60, 120, '직접 입력']
    
    # 기본값이 보기에 있으면 해당 인덱스 사용
    try:
        idx = options.index(default_val)
    except ValueError:
        idx = 4 # 직접 입력
        
    choice = st.selectbox(label, options, index=idx, key=f"{key}_sel")
    
    if choice == '직접 입력':
        val = st.number_input(f"{label} 값 입력", min_value=1, value=default_val, step=1, key=f"{key}_num")
        return val
    else:
        return choice

# --- UI 구성 ---

st.title("Stock Backtesting & Scanner")
st.markdown("---")

# 1. 사이드바 설정
with st.sidebar:
    uploaded_file = st.file_uploader("", type=['csv'])
    st.header("시장 및 기간 설정")
    
    market_select = st.radio("시장 선택", ["KOSPI", "KOSDAQ", "전체"])
    
    st.subheader("기간 설정")
    start_date = st.date_input("시작일(15-02-17)", datetime.date.today() - datetime.timedelta(days=365))
    end_date = st.date_input("종료일", datetime.date.today())
    
    st.markdown("---")

    st.subheader("검색 조건 설정")
    condition_params = {}

    # 1. 이평선(일)
    st.markdown("##### #1. 이평선 배열")
    use_ma = st.checkbox("이동평균선 정배열/역배열 조건", value=True)
    if use_ma:
        col1, col2, col3 = st.columns(3)
        # 기본값: 20 > 60 > 120 (정배열)
        with col1:
            ma1 = render_ma_input("MA_1", 20, "ma1")
        with col2:
            ma2 = render_ma_input("MA_2", 60, "ma2")
        with col3:
            ma3 = render_ma_input("MA_3", 120, "ma3")
        condition_params['ma'] = {'ma1': ma1, 'ma2': ma2, 'ma3': ma3}
        st.caption(f"조건: MA{ma1} > MA{ma2} > MA{ma3}")

    st.markdown("---")

    # 2. 이평선 돌파(일) - MA Cross
    st.markdown("##### #2. 이평선간 돌파")
    use_ma_cross = st.checkbox("이동평균선간 돌파 조건")
    if use_ma_cross:
        col1, col2, col3 = st.columns(3)
        with col1:
            cross_ma1 = render_ma_input("MA (Left)", 20, "cross_ma1")
        with col2:
            cross_op = st.selectbox("비교", ['>', '<'], key='cross_op')
        with col3:
            cross_ma2 = render_ma_input("MA (Right)", 60, "cross_ma2")
            
        condition_params['ma_cross'] = {'ma1': cross_ma1, 'operator': cross_op, 'ma2': cross_ma2}
        st.caption(f"조건: MA{cross_ma1} {cross_op} MA{cross_ma2}")

    st.markdown("---")

    # 3. 주가 돌파(일)
    st.markdown("##### #3. 주가-이평선 돌파")
    use_breakout = st.checkbox("주가의 이동평균선 돌파 조건")
    if use_breakout:
        col1, col2, col3 = st.columns(3)
        with col1:
            price_type = st.selectbox("기준 가격", ['종가', '시가'])
        with col2:
            operator = st.selectbox("비교", ['>', '<'])
        with col3:
            target_ma = render_ma_input("이평선", 20, "breakout_ma")
        condition_params['breakout'] = {'price_type': price_type, 'operator': operator, 'target_ma': target_ma}
        st.caption(f"조건: 당일 {price_type} {operator} MA{target_ma}")

    st.markdown("---")

    # 4. 주가 등락(일)
    st.markdown("##### #4. 주가 당일 등락")
    use_change = st.checkbox("주가 당일 등락률 조건")
    if use_change:
        col1, col2 = st.columns(2)
        change_range = col1.selectbox("등락률 범위", ['3~5', '5~7', '7~9', '9이상'])
        direction = col2.selectbox("방향", ['상승', '하락'])
        condition_params['change'] = {'range': change_range, 'direction': direction}
        st.caption(f"조건: 전일 대비 {change_range}% {direction}")

    st.markdown("---")

    # 5. 거래량(일)
    st.markdown("##### #5. 전일 대비 거래량")
    use_volume = st.checkbox("전일 대비 거래량 변동성 조건")
    if use_volume:
        col1, col2 = st.columns(2)
        vol_range = col1.selectbox("변동성 범위", ['100~200', '200~300', '300이상'])
        vol_direction = col2.selectbox("거래량 추이", ['상승', '하락'])
        condition_params['volume'] = {'range': vol_range, 'direction': vol_direction}
        st.caption(f"조건: 전일 대비 거래량 {vol_range}% {vol_direction}")
    
    st.markdown("---")

    col_n1, col_n2 = st.columns(2)
    with col_n1:
        n_days = st.number_input("N일 후 수익률 확인", min_value=1, value=5)

# 2. 메인 기능 탭
tab1, tab2 = st.tabs(["Stock Backtest", "All Stock Scanning"])

# --- 탭 1: 단일 종목 백테스트 ---
with tab1:
    st.markdown("### 설정한 조건에서 검색한 종목의 승률 및 수익률을 확인합니다.")
    st.markdown("")
    
    stock_list = get_stock_list(market_select, uploaded_file)
    # 검색 편의를 위해 "종목명 (코드)" 형식으로 리스트 생성
    stock_choices = stock_list.apply(lambda x: f"{x['Name']} ({x['Code']})", axis=1)
    selected_stock_str = st.selectbox("종목 검색", stock_choices)

    st.markdown("")
    
    if st.button("백테스팅 시작", key='single_btn'):
        st.session_state['single_backtest_active'] = True

    if st.session_state.get('single_backtest_active', False):
        name = selected_stock_str.split(' (')[0]
        code = selected_stock_str.split(' (')[1][:-1]
        
        with st.spinner(f'{name} 데이터를 분석 중입니다...'):
            result_df, df = backtest_single_stock(code, name, start_date, end_date, condition_params, n_days)
            
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
    st.markdown("### 검색 범위 중 설정한 조건 하에 승률 50% 이상인 종목만 추출합니다.")
    st.info("⚠️ 전체 종목 검색은 시간이 오래 걸릴 수 있어, 시가총액 순 검색을 권장합니다.")
    st.markdown("")

    
    # 세션 상태 초기화 및 동기화 키 설정
    if 'scan_limit' not in st.session_state:
        st.session_state['scan_limit'] = 50
    if 'limit_slider' not in st.session_state:
        st.session_state['limit_slider'] = 50
    if 'limit_num' not in st.session_state:
        st.session_state['limit_num'] = 50

    def update_limit_slider():
        st.session_state['scan_limit'] = st.session_state['limit_slider']
        st.session_state['limit_num'] = st.session_state['limit_slider']
        
    def update_limit_num():
        st.session_state['scan_limit'] = st.session_state['limit_num']
        st.session_state['limit_slider'] = st.session_state['limit_num']

    # 전체 종목 수 계산 (최대값 설정을 위해)
    # 캐싱된 함수 호출로 성능 부하 최소화
    current_stock_list = get_stock_list(market_select, uploaded_file)
    total_stock_count = len(current_stock_list) if not current_stock_list.empty else 200

    col_l1, col_l2 = st.columns([5, 1])
    with col_l1:
        st.slider("검색 대상 종목 수(시가총액 순)", 10, total_stock_count, key='limit_slider', on_change=update_limit_slider)
    with col_l2:
        st.number_input("수치 조정", 10, total_stock_count, key='limit_num', on_change=update_limit_num)
        
    limit_num = st.session_state['scan_limit']
    
    st.markdown("")



    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
    with col_btn1:
        start_scan = st.button("시가총액 상위 종목 검색", key='scan_top', use_container_width=True)
    with col_btn2:
        start_all = st.button("전체 종목 검색", key='scan_all', use_container_width=True)
    with col_btn3:
        stop_scan = st.button("검색 중지", key='stop_scan', use_container_width=True)

    if stop_scan:
        st.warning("검색이 중지되었습니다.")
        st.stop()

    if start_scan or start_all:
        stock_list = get_stock_list(market_select, uploaded_file)
        
        if start_all:
            target_stocks = stock_list
            st.info(f"선택한 시장의 전체 종목 ({len(target_stocks)}개)을 검색합니다. 시간이 오래 걸릴 수 있습니다.")
        else:
            # 상위 N개만 테스트
            target_stocks = stock_list.head(limit_num)
            st.info(f"시가총액 상위 {limit_num}개 종목을 검색합니다.")
        
        final_results = []
        progress_bar = st.progress(0)
        
        status_text = st.empty()
        
        for idx, row in target_stocks.iterrows():
            # 진행률 표시
            progress = (idx + 1) / len(target_stocks)
            progress_bar.progress(progress)
            status_text.text(f"분석 중: {row['Name']} ({idx+1}/{len(target_stocks)})")
            
            # 개별 종목 백테스트 실행 (df는 스캔에서 불필요)
            res, _ = backtest_single_stock(row['Code'], row['Name'], start_date, end_date, condition_params, n_days)
            
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
            
            # 승률 50% 이상 필터링
            filtered_summary = result_summary[result_summary['승률(%)'] >= 50.0]
            
            st.write(f"검색 결과: 총 {len(filtered_summary)}개 종목 발견 (승률 50% 이상)")
            st.dataframe(filtered_summary, use_container_width=True)
        else:
            st.warning("조건을 만족하는 종목을 찾지 못했습니다.")