import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os
try:
    import OpenDartReader
except ImportError:
    OpenDartReader = None

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

    # 1.5 이평선 돌파(일) - MA Cross (크로스 발생 시점 체크)
    if 'ma_cross' in params:
        p = params['ma_cross']
        ma1_col = df[f'MA{p["ma1"]}']
        ma2_col = df[f'MA{p["ma2"]}']
        
        # 이전 날짜 데이터 (shift 1)
        prev_ma1 = ma1_col.shift(1)
        prev_ma2 = ma2_col.shift(1)
        
        if p['operator'] == '>':
            # 골든크로스: 어제는 ma1 < ma2 였다가, 오늘은 ma1 > ma2
            mask = (prev_ma1 <= prev_ma2) & (ma1_col > ma2_col)
        else:
            # 데드크로스: 어제는 ma1 > ma2 였다가, 오늘은 ma1 < ma2
            mask = (prev_ma1 >= prev_ma2) & (ma1_col < ma2_col)
            
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
        
    # 6. 기본적 분석 (전처리된 컬럼 사용)
    if 'fundamental' in params and 'Fundamental' in df.columns:
        combined_mask = combined_mask & df['Fundamental']

    return combined_mask

@st.cache_data
def get_fundamental_data(api_key, stock_code, start_year, end_year):
    """OpenDartReader를 사용하여 재무제표 데이터를 가져옵니다."""
    if not api_key:
        return None
    
    try:
        dart = OpenDartReader(api_key)
        # 최근 3~4년 데이터 조회 (분기보고서 포함)
        # 11013: 1분기, 11012: 반기, 11014: 3분기, 11011: 사업보고서
        report_codes = ['11013', '11012', '11014', '11011']
        
        all_data = []
        for year in range(start_year, end_year + 1):
            for code in report_codes:
                try:
                    df = dart.finstate(corp=stock_code, bsns_year=str(year), reprt_code=code)
                    if df is not None and not df.empty:
                        # 1. CFS(연결) 우선, 없으면 OFS(별도) 사용
                        if 'CFS' in df['fs_div'].unique():
                            df = df[df['fs_div'] == 'CFS']
                        else:
                            df = df[df['fs_div'] == 'OFS']
                        
                        # 2. 계정명 표준화 (동의어 처리)
                        # 목표 계정: 매출액, 영업이익, 당기순이익, 자본총계, 부채총계, 영업활동현금흐름, 유형자산의취득
                        
                        # 표준화 함수
                        def normalize_account_nm(nm):
                            nm = nm.replace(' ', '') # 공백 제거
                            if nm in ['매출액', '수익(매출액)', '매출']: return '매출액'
                            if nm in ['영업이익', '영업이익(손실)']: return '영업이익'
                            if nm in ['당기순이익', '당기순이익(손실)', '연결당기순이익', '법인세비용차감전계속영업이익']: return '당기순이익' # 법인세...는 차선책
                            if nm in ['자본총계', '자본']: return '자본총계'
                            if nm in ['부채총계', '부채']: return '부채총계'
                            if '영업활동' in nm and '현금흐름' in nm: return '영업활동현금흐름' # 영업활동으로인한현금흐름 등
                            if '유형자산' in nm and ('취득' in nm or '증가' in nm): return '유형자산의취득' # 유형자산의 취득, 유형자산의증가
                            return nm
                            
                        df['account_nm_norm'] = df['account_nm'].apply(normalize_account_nm)
                        
                        target_accounts = ['매출액', '영업이익', '당기순이익', '자본총계', '부채총계', '영업활동현금흐름', '유형자산의취득']
                        
                        # 필터링
                        df_filtered = df[df['account_nm_norm'].isin(target_accounts)].copy()
                        
                        if not df_filtered.empty:
                            # 중복 제거 (같은 표준 명칭이 여러 개일 경우, 첫 번째 것 사용하거나 우선순위)
                            df_filtered = df_filtered.drop_duplicates(subset=['account_nm_norm'], keep='first')
                            
                            df_filtered['account_nm'] = df_filtered['account_nm_norm'] # 표준 명칭으로 덮어쓰기
                            df_filtered = df_filtered.drop(columns=['account_nm_norm'])
                            
                            df_filtered['year'] = year
                            df_filtered['reprt_code'] = code
                            all_data.append(df_filtered)
                except:
                    continue
                    
        if not all_data:
            return None
            
        final_df = pd.concat(all_data)
        return final_df
    except Exception as e:
        return None

def calculate_growth_mask(df_main, item_name, period_type, n_percent, date_index):
    """
    특정 항목의 성장률 조건을 만족하는지 확인하는 마스크 생성
    period_type: 'year' or 'quarter'
    """
    # 데이터 준비
    df_item = df_main[df_main['account_nm'] == item_name].copy()
    if df_item.empty:
        return pd.Series(False, index=date_index)
        
    df_item = df_item.sort_values('release_date')
    
    # 연간/분기 구분
    if period_type == 'year':
        # 사업보고서(11011)만 필터링
        df_target = df_item[df_item['reprt_code'] == '11011'].copy()
        df_target = df_target.sort_values('year')
        df_target = df_target.drop_duplicates(subset=['year'], keep='last')
        
    else: # quarter
        # 모든 보고서 사용 (단순 시계열)
        df_target = df_item.sort_values('release_date')

    # 결과 마스크
    result_mask = pd.Series(False, index=date_index)
    
    values = df_target['amount'].values
    dates = df_target['release_date'].values
    
    if len(values) < 4: 
        return pd.Series(False, index=date_index)

    # 마스크 업데이트
    for i in range(3, len(values)):
        v0, v1, v2, v3 = values[i-3], values[i-2], values[i-1], values[i]
        
        # 성장률 조건 (n% 이상)
        try:
            g1 = (v1 - v0) / abs(v0) * 100 if v0 != 0 else 0
            g2 = (v2 - v1) / abs(v1) * 100 if v1 != 0 else 0
            g3 = (v3 - v2) / abs(v2) * 100 if v2 != 0 else 0
            
            if g1 >= n_percent and g2 >= n_percent and g3 >= n_percent:
                start_dt = dates[i]
                next_dt = dates[i+1] if i+1 < len(dates) else None
                
                if next_dt:
                    result_mask.loc[(result_mask.index >= start_dt) & (result_mask.index < next_dt)] = True
                else:
                     result_mask.loc[result_mask.index >= start_dt] = True
                     
        except:
            pass
            
    return result_mask

def calculate_surplus_mask(df_main, item_name, period_type, date_index):
    """
    특정 항목의 흑자(>0) 지속 여부를 확인하는 마스크 생성
    """
    df_item = df_main[df_main['account_nm'] == item_name].copy()
    if df_item.empty:
        return pd.Series(False, index=date_index)
        
    df_item = df_item.sort_values('release_date')
    
    if period_type == 'year':
        df_target = df_item[df_item['reprt_code'] == '11011'].copy()
        df_target = df_target.sort_values('year')
        df_target = df_target.drop_duplicates(subset=['year'], keep='last')
    else: 
        df_target = df_item.sort_values('release_date')

    result_mask = pd.Series(False, index=date_index)
    
    values = df_target['amount'].values
    dates = df_target['release_date'].values
    
    if len(values) < 3: 
        return pd.Series(False, index=date_index)

    # 3년/3분기 연속 흑자
    for i in range(2, len(values)):
        v0, v1, v2 = values[i-2], values[i-1], values[i]
        
        if v0 > 0 and v1 > 0 and v2 > 0:
            start_dt = dates[i]
            next_dt = dates[i+1] if i+1 < len(dates) else None
            
            if next_dt:
                result_mask.loc[(result_mask.index >= start_dt) & (result_mask.index < next_dt)] = True
            else:
                 result_mask.loc[result_mask.index >= start_dt] = True
            
    return result_mask

def process_fundamental_data(date_index, fund_df, params):
    """
    params에 담긴 여러 조건들(매출_3y, 매출_3q, 부채비율 등)을 모두 만족하는지 AND 연산
    """
    if fund_df is None or fund_df.empty:
        return pd.Series(False, index=date_index)
        
    # 금액 컬럼 수치화
    fund_df['amount'] = pd.to_numeric(fund_df['thstrm_amount'].str.replace(',', ''), errors='coerce')
    
    # 영업이익률 계산 및 추가 (매출액, 영업이익 존재 시)
    try:
        # 피벗으로 날짜/리포트별 매칭
        df_p = fund_df.pivot_table(index=['year', 'reprt_code'], columns='account_nm', values='amount', aggfunc='mean').reset_index()
        if '매출액' in df_p.columns and '영업이익' in df_p.columns:
            df_p['영업이익률'] = df_p.apply(lambda x: (x['영업이익'] / x['매출액'] * 100) if x['매출액'] != 0 else 0, axis=1)
            
            # 원래 형식으로 변환하여 병합 (account_nm: '영업이익률', amount: 계산값)
            # 다른 컬럼(thstrm_dt 등)은 누락되지만 calculate_growth_mask는 account_nm, amount, year, reprt_code만 씀 (release_date는 나중에 merge or re-cal)
            # release_date를 위해 원본의 메타데이터가 필요함.
            # 복잡하므로, fund_df에 있는 release_date 로직을 먼저 수행하고, 그 뒤에 병합
            pass
    except:
        pass

    # 공시일 계산
    def get_release_date(row):
        y = int(row['year'])
        rc = row['reprt_code']
        if rc == '11013': return pd.Timestamp(f"{y}-05-15")
        elif rc == '11012': return pd.Timestamp(f"{y}-08-14")
        elif rc == '11014': return pd.Timestamp(f"{y}-11-14")
        elif rc == '11011': return pd.Timestamp(f"{y+1}-03-31")
        return pd.Timestamp(f"{y}-12-31")

    fund_df['release_date'] = fund_df.apply(get_release_date, axis=1)

    # FCF 계산 (영업활동현금흐름 - 유형자산취득)
    # 유형자산취득은 보통 음수(-)로 표시되거나 양수(+)로 표시됨. (OpenDart 확인 필요하지만, 보통 현금유출은 차감해야 함)
    # 재무제표상 '취득'은 현금 유출이므로, 만약 양수로 표기되어 있다면 OCF - Capex.
    # 만약 음수로 표기되어 있다면 OCF + Capex.
    # 안전하게: OCF - abs(Capex)
    try:
        df_ocf = fund_df[fund_df['account_nm']=='영업활동현금흐름'][['year', 'reprt_code', 'amount', 'release_date']].rename(columns={'amount': 'ocf'})
        # 유형자산의취득이 없으면 0 처리
        df_capex = fund_df[fund_df['account_nm']=='유형자산의취득'][['year', 'reprt_code', 'amount']].rename(columns={'amount': 'capex'})
        
        if not df_ocf.empty:
            if df_capex.empty:
                df_fcf = df_ocf.copy()
                df_fcf['fcf'] = df_fcf['ocf']
            else:
                df_fcf = pd.merge(df_ocf, df_capex, on=['year', 'reprt_code'], how='left').fillna(0)
                # Capex is outflow. FCF = OCF - Capital Expenditures.
                # Assuming 'amount' is absolute value for acquisition in notes, but in CFS statement it might be negative.
                # Let's assume absolute magnitude subtraction for simplicity in Beta.
                df_fcf['fcf'] = df_fcf['ocf'] - df_fcf['capex'].abs()
            
            df_fcf['account_nm'] = 'FCF'
            df_fcf['amount'] = df_fcf['fcf']
            df_fcf = df_fcf[['year', 'reprt_code', 'account_nm', 'amount', 'release_date']]
            fund_df = pd.concat([fund_df, df_fcf], ignore_index=True)
    except:
        pass
    
    # 영업이익률 데이터 생성
    try:
        df_rev = fund_df[fund_df['account_nm']=='매출액'][['year', 'reprt_code', 'amount', 'release_date']].rename(columns={'amount': 'rev'})
        df_op = fund_df[fund_df['account_nm']=='영업이익'][['year', 'reprt_code', 'amount']].rename(columns={'amount': 'op'})
        
        df_margin = pd.merge(df_rev, df_op, on=['year', 'reprt_code'], how='inner')
        df_margin['amount'] = df_margin.apply(lambda x: (x['op'] / x['rev'] * 100) if x['rev'] != 0 else 0, axis=1)
        df_margin['account_nm'] = '영업이익률'
        
        # 필요한 컬럼만 선택해서 fund_df에 추가
        df_margin = df_margin[['year', 'reprt_code', 'account_nm', 'amount', 'release_date']]
        
        fund_df = pd.concat([fund_df, df_margin], ignore_index=True)
    except:
        pass
    
    # 전체 마스크 (True로 시작)
    final_mask = pd.Series(True, index=date_index)
    
    # 항목 매핑
    item_map = {
        'rev': '매출액',
        'op': '영업이익',
        'net': '당기순이익',
        'margin': '영업이익률',
        'fcf': 'FCF'
    }
    
    for key, val in params.items():
        if val is None: continue 
        if key == 'api_key': continue
        
        # 부채비율 별도 처리
        if key == 'debt_ratio':
            df_equity = fund_df[fund_df['account_nm'] == '자본총계'].sort_values('release_date')
            df_liab = fund_df[fund_df['account_nm'] == '부채총계'].sort_values('release_date')
            
            # Simple merge/pivot mechanism
            # Using unique dates from equity/liab union
            all_dates = sorted(list(set(df_equity['release_date']) | set(df_liab['release_date'])))
            
            ratio_mask = pd.Series(False, index=date_index)
            
            # Iterate through time, finding latest equity/liab
            # Better: pivot table
            df_pivot = fund_df.pivot_table(index='release_date', columns='account_nm', values='amount', aggfunc='last').sort_index()
            
            dates = df_pivot.index
            for i in range(len(dates)):
                try:
                    row = df_pivot.iloc[i]
                    # Forward fill missing values manually if needed, usually pivot makes NaNs if missing
                    # Just skip if both not present? Or assume previous?
                    # Let's assume data is present in same report.
                    equity = row.get('자본총계')
                    liab = row.get('부채총계')
                    
                    if pd.notna(equity) and pd.notna(liab) and equity > 0:
                        ratio = (liab / equity) * 100
                        if ratio <= val:
                            start_dt = dates[i]
                            next_dt = dates[i+1] if i+1 < len(dates) else None
                            if next_dt:
                                ratio_mask.loc[(ratio_mask.index >= start_dt) & (ratio_mask.index < next_dt)] = True
                            else:
                                ratio_mask.loc[ratio_mask.index >= start_dt] = True
                except:
                    pass
            
            final_mask = final_mask & ratio_mask

        # 성장성/흑자 조건 (매출, 영업이익, 순이익, FCF)
        elif '3y' in key or '3q' in key:
            prefix = key.split('_')[0] 
            period = 'year' if '3y' in key else 'quarter'
            
            if prefix in item_map:
                item_name = item_map[prefix]
                
                # FCF 흑자 체크인 경우 (val=0, growth calculation logic handles continuous check?)
                # calculate_growth_mask는 성장률(n%) 체크임.
                # 흑자 지속 체크를 위해서는 n%가 아니라 > 0 조건 필요.
                # 기존 함수 재사용: n_percent를 특별한 값(예: -999)으로 주거나 새로운 함수 필요?
                # -> FCF 흑자 지속 요청.
                # calculate_growth_mask 수정 혹은 별도 처리 필요.
                # 일단 여기서는 FCF 흑자(Growth 아님)를 처리해야 함.
                if prefix == 'fcf':
                     # 별도 함수 없이, calculate_growth_mask를 '흑자' 모드로 사용?
                     # 함수 내부 로직이 (v1-v0)/v0 >= n 이라서 흑자와는 다름.
                     # FCF용 별도 로직 구현
                     mask = calculate_surplus_mask(fund_df, item_name, period, date_index)
                     final_mask = final_mask & mask
                else:
                    mask = calculate_growth_mask(fund_df, item_name, period, val, date_index)
                    final_mask = final_mask & mask

    return final_mask

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
    
    # 기본적 분석 데이터 처리
    # Tab 2에서만 condition['fundamental']이 들어올 것임.
    if 'fundamental' in condition:
        fund_params = condition['fundamental']
        if fund_params.get('api_key'): # API Key가 있어야 실행
            # 백테스트 기간보다 더 이전 데이터 필요 (3년치)
            fund_start_year = start_date.year - 4
            fund_end_year = end_date.year
            
            fund_df = get_fundamental_data(fund_params['api_key'], code, fund_start_year, fund_end_year)
            
            if fund_df is None or fund_df.empty:
                df['Fundamental'] = False
            else:
                df['Fundamental'] = process_fundamental_data(df.index, fund_df, fund_params)
        else:
             df['Fundamental'] = False
    
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

def load_api_key_from_file():
    try:
        if os.path.exists("opendart_api_key.txt"):
            with open("opendart_api_key.txt", "r", encoding="utf-8") as f:
                return f.read().strip()
    except:
        pass
    return ""

# 1. 사이드바 설정
with st.sidebar:
    uploaded_file = st.file_uploader("", type=['csv'])
    
    st.markdown("### OpenDart 설정 (기본적 분석)")
    default_api_key = load_api_key_from_file()
    opendart_api_key = st.text_input("OpenDart API Key", value=default_api_key, type="password", help="OpenDart API Key가 필요합니다.")
    if not default_api_key:
         st.caption("💡 'opendart_api_key.txt' 파일을 생성하여 키를 저장하면 자동 입력됩니다.")

    st.header("시장 및 기간 설정")
    
    market_select = st.radio("시장 선택", ["KOSPI", "KOSDAQ", "전체"])
    
    st.subheader("기간 설정")
    today = datetime.date.today()
    min_date = datetime.date(2000, 1, 1)

    start_date = st.date_input("시작일(00-01-01부터)", value=today - datetime.timedelta(days=365), min_value=min_date, max_value=today)
    end_date = st.date_input("종료일(오늘까지)", value=today, min_value=min_date, max_value=today)
    
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

    if 'fundamental' in condition_params:
        del condition_params['fundamental']

    # 6. 기본적 분석 (재무제표) - Tab 2 내부로 이동됨
    
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
                # 차트 삭제 요청으로 인해 주석 처리 또는 제거
                # st.plotly_chart(fig, use_container_width=True)
                
                # --- 기본적 분석 결과 리포트 ---
                st.markdown("### 📊 기본적 분석 리포트")
                if not opendart_api_key:
                    st.warning("OpenDart API Key가 입력되지 않아 기본적 분석 결과를 표시할 수 없습니다.")
                else:
                    # 데이터 가져오기 (3년전 ~ 현재)
                    fund_start_year = start_date.year - 4
                    fund_end_year = end_date.year
                    fund_df = get_fundamental_data(opendart_api_key, code, fund_start_year, fund_end_year)
                    
                    if fund_df is None or fund_df.empty:
                        st.error("재무 데이터를 가져올 수 없습니다.")
                    else:
                        # 데이터 전처리 (금액 수치화, FCF 계산, 영업이익률 계산, 공시일 계산 등)
                        # process_fundamental_data 내부 로직 일부 재사용하거나 별도 처리
                        # 여기서는 화면 표시용이므로 직관적으로 계산
                        
                        # 1. 전처리
                        fund_df['amount'] = pd.to_numeric(fund_df['thstrm_amount'].str.replace(',', ''), errors='coerce')
                        
                        # 공시일(release_date)
                        def get_release_date_local(row):
                            y = int(row['year'])
                            rc = row['reprt_code']
                            if rc == '11013': return pd.Timestamp(f"{y}-05-15")
                            elif rc == '11012': return pd.Timestamp(f"{y}-08-14")
                            elif rc == '11014': return pd.Timestamp(f"{y}-11-14")
                            elif rc == '11011': return pd.Timestamp(f"{y+1}-03-31")
                            return pd.Timestamp(f"{y}-12-31")
                        fund_df['release_date'] = fund_df.apply(get_release_date_local, axis=1)

                        # FCF 추가
                        try:
                            df_ocf = fund_df[fund_df['account_nm']=='영업활동현금흐름'][['year', 'reprt_code', 'amount']].rename(columns={'amount': 'ocf'})
                            df_capex = fund_df[fund_df['account_nm']=='유형자산의취득'][['year', 'reprt_code', 'amount']].rename(columns={'amount': 'capex'})
                            
                            df_fcf = pd.merge(df_ocf, df_capex, on=['year', 'reprt_code'], how='left').fillna(0)
                            df_fcf['amount'] = df_fcf['ocf'] - df_fcf['capex'].abs()
                            df_fcf['account_nm'] = 'FCF'
                            # release_date 등 병합 생략하고 concat용으로 최소화
                            # year/report_code로 원본 merge해서 release_date 가져오기
                            df_fcf = pd.merge(df_fcf, fund_df[['year', 'reprt_code', 'release_date']].drop_duplicates(), on=['year', 'reprt_code'], how='left')
                            fund_df = pd.concat([fund_df, df_fcf], ignore_index=True)
                        except: pass

                        # 영업이익률 추가
                        try:
                            df_rev = fund_df[fund_df['account_nm']=='매출액'][['year', 'reprt_code', 'amount']].rename(columns={'amount': 'rev'})
                            df_op = fund_df[fund_df['account_nm']=='영업이익'][['year', 'reprt_code', 'amount']].rename(columns={'amount': 'op'})
                            df_margin = pd.merge(df_rev, df_op, on=['year', 'reprt_code'], how='inner')
                            df_margin['amount'] = df_margin.apply(lambda x: (x['op'] / x['rev'] * 100) if x['rev'] != 0 else 0, axis=1)
                            df_margin['account_nm'] = '영업이익률'
                            df_margin = pd.merge(df_margin, fund_df[['year', 'reprt_code', 'release_date']].drop_duplicates(), on=['year', 'reprt_code'], how='left')
                            fund_df = pd.concat([fund_df, df_margin], ignore_index=True)
                        except: pass

                        # 체크 리스트
                        check_items = [
                            ("매출액 추이 (3년 연속 상승)", '매출액', 'year', 'growth'),
                            ("매출액 추이 (3분기 연속 상승)", '매출액', 'quarter', 'growth'),
                            ("영업이익 추이 (3년 연속 상승)", '영업이익', 'year', 'growth'),
                            ("영업이익 추이 (3분기 연속 상승)", '영업이익', 'quarter', 'growth'),
                            ("영업이익률 추이 (3년 연속 상승)", '영업이익률', 'year', 'growth'),
                            ("영업이익률 추이 (3분기 연속 상승)", '영업이익률', 'quarter', 'growth'),
                            ("당기순이익 추이 (3년 연속 상승)", '당기순이익', 'year', 'growth'),
                            ("당기순이익 추이 (3분기 연속 상승)", '당기순이익', 'quarter', 'growth'),
                            ("FCF (3년 연속 흑자)", 'FCF', 'year', 'surplus'),
                            ("FCF (3분기 연속 흑자)", 'FCF', 'quarter', 'surplus'),
                        ]
                        
                        results = []
                        
                        # Growth/Surplus Check Function
                        def check_status(item, period, mode):
                            df_item = fund_df[fund_df['account_nm'] == item].copy()
                            if df_item.empty: return "데이터 없음"
                            
                            if period == 'year':
                                df_target = df_item[df_item['reprt_code'] == '11011'].sort_values('year').drop_duplicates(['year'], keep='last')
                            else:
                                df_target = df_item.sort_values('release_date')
                                
                            vals = df_target['amount'].values
                            if len(vals) < 4: return "데이터 부족"
                            
                            # 최근 4개 (v0 -> v1 -> v2 -> v3(최근))
                            v = vals[-4:]
                            v0, v1, v2, v3 = v[0], v[1], v[2], v[3]
                            
                            if mode == 'growth':
                                # 단순 상승 여부 (>0 성장)
                                try:
                                    cond = (v1 > v0) and (v2 > v1) and (v3 > v2)
                                    return "✅ 만족" if cond else "❌ 불만족"
                                except: return "계산 오류"
                            elif mode == 'surplus':
                                # 흑자 지속 (값 > 0) -> 최근 3개만 보면 됨? "연속 3년/3분기"
                                # v1, v2, v3가 0보다 큰지
                                try:
                                    cond = (v1 > 0) and (v2 > 0) and (v3 > 0)
                                    return "✅ 만족" if cond else "❌ 불만족"
                                except: return "계산 오류"
                        
                        for label, item, period, mode in check_items:
                            status = check_status(item, period, mode)
                            results.append((label, status))
                            
                        # 부채비율 (최근 분기 100% 이하)
                        try:
                            df_liab = fund_df[fund_df['account_nm']=='부채총계'].sort_values('release_date')
                            df_eq = fund_df[fund_df['account_nm']=='자본총계'].sort_values('release_date')
                            if not df_liab.empty and not df_eq.empty:
                                try:
                                    last_liab = df_liab.iloc[-1]['amount']
                                    last_eq = df_eq.iloc[-1]['amount']
                                    if last_eq > 0:
                                        debt_ratio = (last_liab / last_eq) * 100
                                        debt_status = "✅ 만족" if debt_ratio <= 100 else f"❌ 불만족 ({debt_ratio:.1f}%)"
                                    else:
                                        debt_status = "자본잠식"
                                except: debt_status = "데이터 오류"
                            else: debt_status = "데이터 없음"
                        except: debt_status = "데이터 없음"
                        
                        results.append(("부채비율 (최근 분기 100% 이하)", debt_status))
                        
                        # 결과 출력
                        st.table(pd.DataFrame(results, columns=["항목", "결과"]))

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



    # --- 기본적 분석 UI (Tab 2 전용) ---
    st.markdown("##### #6. 기본적 분석 (Beta) - Tab 2 전용")
    use_fundamental = st.checkbox("재무제표 조건 적용", key='use_fund_tab2')
    
    fund_conditions_tab2 = {}
    
    if use_fundamental:
        if not opendart_api_key:
            st.error("OpenDart API Key를 먼저 입력해주세요 (사이드바).")
        else:
            fund_conditions_tab2 = {'api_key': opendart_api_key}
            
            with st.expander("재무제표 상세 조건 설정", expanded=True):
                st.markdown("**매출액 (Revenue)**")
                c1, c2 = st.columns(2)
                if c1.checkbox("3년 연속 상승", key='t2_rev_3y'):
                    v = c1.number_input("매출 3년 상승률(%)", value=0, key='t2_input_rev_3y')
                    fund_conditions_tab2['rev_3y'] = v
                if c2.checkbox("3분기 연속 상승", key='t2_rev_3q'):
                    v = c2.number_input("매출 3분기 상승률(%)", value=0, key='t2_input_rev_3q')
                    fund_conditions_tab2['rev_3q'] = v
                    
                st.markdown("**영업이익 (Op. Income)**")
                c3, c4 = st.columns(2)
                if c3.checkbox("3년 연속 상승", key='t2_op_3y'):
                    v = c3.number_input("영업이익 3년 상승률(%)", value=0, key='t2_input_op_3y')
                    fund_conditions_tab2['op_3y'] = v
                if c4.checkbox("3분기 연속 상승", key='t2_op_3q'):
                    v = c4.number_input("영업이익 3분기 상승률(%)", value=0, key='t2_input_op_3q')
                    fund_conditions_tab2['op_3q'] = v

                st.markdown("**영업이익률 (Op. Margin)**")
                c_om1, c_om2 = st.columns(2)
                if c_om1.checkbox("3년 연속 상승", key='t2_om_3y'):
                    v = c_om1.number_input("이익률 3년 상승률(%)", value=0, key='t2_input_om_3y')
                    fund_conditions_tab2['margin_3y'] = v
                if c_om2.checkbox("3분기 연속 상승", key='t2_om_3q'):
                    v = c_om2.number_input("이익률 3분기 상승률(%)", value=0, key='t2_input_om_3q')
                    fund_conditions_tab2['margin_3q'] = v

                st.markdown("**당기순이익 (Net Income)**")
                c5, c6 = st.columns(2)
                if c5.checkbox("3년 연속 상승", key='t2_net_3y'):
                    v = c5.number_input("순이익 3년 상승률(%)", value=0, key='t2_input_net_3y')
                    fund_conditions_tab2['net_3y'] = v
                if c6.checkbox("3분기 연속 상승", key='t2_net_3q'):
                    v = c6.number_input("순이익 3분기 상승률(%)", value=0, key='t2_input_net_3q')
                    fund_conditions_tab2['net_3q'] = v
                
                st.markdown("**FCF (잉여현금흐름)**")
                c_fcf1, c_fcf2 = st.columns(2)
                if c_fcf1.checkbox("3년 연속 흑자", key='t2_fcf_3y'):
                    fund_conditions_tab2['fcf_3y'] = 0 # 0 means surplus check
                if c_fcf2.checkbox("3분기 연속 흑자", key='t2_fcf_3q'):
                    fund_conditions_tab2['fcf_3q'] = 0

                st.markdown("**부채비율 (Debt Ratio)**")
                if st.checkbox("부채비율 제한", key='t2_debt'):
                    debt_limit = st.number_input("부채비율(%) 이하", value=100, step=10, key='t2_input_debt')
                    fund_conditions_tab2['debt_ratio'] = debt_limit


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
        # Tab 2 전용 조건을 통합
        scan_conditions = condition_params.copy()
        if fund_conditions_tab2:
            scan_conditions['fundamental'] = fund_conditions_tab2
            
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
            res, _ = backtest_single_stock(row['Code'], row['Name'], start_date, end_date, scan_conditions, n_days)
            
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

        # 결과 요약 (scan_conditions 사용)
        
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