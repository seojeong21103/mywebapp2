import streamlit as st

st.title("첫 배포 확인 👋")
st.write("여기까지 보이면 배포 성공입니다.")
import streamlit as st
import pandas as pd
import json
import urllib.request
import io
import re

# =========================================================
# 기본 설정
# =========================================================

st.set_page_config(
    page_title="전국 고령화 지도",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ 전국 고령화 지도")
st.caption("시군구별 만 65세 이상 인구 비율을 색으로 나타낸 단계구분도")

# =========================================================
# 데이터 주소
# =========================================================

# 행정동/시군구 경계 데이터
GEOJSON_URL = (
    "https://raw.githubusercontent.com/"
    "vuski/admdongkor/master/"
    "ver20260701/sigungu.geojson"
)

# 행정안전부 주민등록 연령별 인구현황
# 공개 GitHub에 저장된 2026년 6월 자료를 사용
CSV_URL = (
    "https://raw.githubusercontent.com/"
    "yeppdal-alt/2026-pop/main/"
    "202606_202606_연령별인구현황_월간.csv"
)


# =========================================================
# 데이터 다운로드
# =========================================================

@st.cache_data(ttl=60 * 60 * 12)
def download_geojson():
    try:
        with urllib.request.urlopen(GEOJSON_URL, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"행정구역 경계 데이터를 불러오지 못했습니다: {e}")


@st.cache_data(ttl=60 * 60 * 12)
def download_population():
    try:
        with urllib.request.urlopen(CSV_URL, timeout=60) as response:
            data = response.read()

        # 행정안전부 원본은 CP949인 경우가 많음
        for encoding in ["cp949", "euc-kr", "utf-8-sig", "utf-8"]:
            try:
                return pd.read_csv(
                    io.BytesIO(data),
                    encoding=encoding
                )
            except UnicodeDecodeError:
                continue

        raise RuntimeError("CSV 인코딩을 확인할 수 없습니다.")

    except Exception as e:
        raise RuntimeError(f"인구 데이터를 불러오지 못했습니다: {e}")


# =========================================================
# 인구 데이터 처리
# =========================================================

def clean_number(value):
    """쉼표 등이 들어간 숫자를 숫자로 변환"""
    if pd.isna(value):
        return 0

    text = str(value)
    text = text.replace(",", "")
    text = text.replace('"', "")
    text = text.strip()

    try:
        return float(text)
    except:
        return 0


def find_age_columns(df):
    """
    65세 이상 연령 컬럼 자동 검색
    """
    columns = list(df.columns)

    total_columns = []
    old_columns = []

    for col in columns:
        col_text = str(col)

        # 전체 인구 컬럼
        if (
            ("계" in col_text or "전체" in col_text)
            and "연령" in col_text
        ):
            total_columns.append(col)

        # 65세 이상
        match = re.search(r"(\d+)세", col_text)

        if match:
            age = int(match.group(1))

            if age >= 65 and (
                "계" in col_text or "전체" in col_text
            ):
                old_columns.append(col)

    return total_columns, old_columns


def make_population_table(df):
    """
    원본 연령별 인구 데이터를
    시군구 단위 고령인구 비율 데이터로 변환
    """

    # 행정구역 컬럼 찾기
    area_col = None

    for col in df.columns:
        if "행정구역" in str(col):
            area_col = col
            break

    if area_col is None:
        area_col = df.columns[0]

    total_columns, old_columns = find_age_columns(df)

    if not total_columns:
        raise RuntimeError(
            "전체 인구 컬럼을 찾지 못했습니다."
        )

    # 가장 적절한 전체 인구 컬럼 선택
    total_col = total_columns[0]

    # 숫자형 변환
    result = pd.DataFrame()

    result["행정구역"] = df[area_col].astype(str)

    result["전체인구"] = df[total_col].apply(clean_number)

    # 65세 이상 컬럼
    if old_columns:
        result["65세이상"] = 0.0

        for col in old_columns:
            result["65세이상"] += df[col].apply(clean_number)

    else:
        # 컬럼명이 예상과 다를 경우
        # 연령 컬럼을 직접 탐색
        age_columns = []

        for col in df.columns:
            text = str(col)

            matches = re.findall(r"(\d+)세", text)

            if matches:
                age = int(matches[-1])

                if age >= 65 and "계" in text:
                    age_columns.append(col)

        if not age_columns:
            raise RuntimeError(
                "65세 이상 인구 컬럼을 찾지 못했습니다."
            )

        result["65세이상"] = 0.0

        for col in age_columns:
            result["65세이상"] += df[col].apply(clean_number)

    # 비율 계산
    result["고령인구비율"] = (
        result["65세이상"]
        / result["전체인구"]
        * 100
    )

    result.loc[
        result["전체인구"] <= 0,
        "고령인구비율"
    ] = None

    return result


# =========================================================
# 행정구역명 정리
# =========================================================

def normalize_name(name):
    """
    행정구역명에서 불필요한 정보 제거
    """

    name = str(name)

    # 괄호 안 행정코드 제거
    name = re.sub(r"\s*\([^)]*\)", "", name)

    # 행정구역 코드가 붙어 있는 경우 제거
    name = re.sub(r"\s*\d{5,10}$", "", name)

    return name.strip()


def make_sigungu_name(name):
    """
    주민등록 데이터의 행정구역명에서
    시군구 이름을 최대한 추출
    """

    name = normalize_name(name)

    # 예:
    # 서울특별시 종로구
    # 경기도 수원시 영통구
    # 충청남도 천안시 동남구

    parts = name.split()

    if len(parts) >= 2:
        # 특별시 / 광역시 / 특별자치시
        if (
            parts[0].endswith("특별시")
            or parts[0].endswith("광역시")
            or parts[0].endswith("특별자치시")
        ):
            return " ".join(parts[:2])

        # 도 지역
        if parts[0].endswith("도"):
            if len(parts) >= 3:
                # 시 + 구
                if parts[2].endswith("구"):
                    return " ".join(parts[:3])

                return " ".join(parts[:2])

    return name


# =========================================================
# GeoJSON 속성 확인
# =========================================================

def find_geo_name_property(geojson):
    """
    GeoJSON에서 지역 이름이 들어 있는 속성을 자동 탐색
    """

    features = geojson.get("features", [])

    if not features:
        raise RuntimeError("GeoJSON에 지역 정보가 없습니다.")

    properties = features[0].get("properties", {})

    candidates = [
        "adm_nm",
        "SIG_KOR_NM",
        "sigungu_nm",
        "name",
        "NAME",
        "sido_sigungu",
        "sgg_nm"
    ]

    for candidate in candidates:
        if candidate in properties:
            return candidate

    # 자동 탐색
    for key, value in properties.items():
        text = str(value)

        if (
            "시" in text
            or "군" in text
            or "구" in text
        ):
            return key

    raise RuntimeError(
        "GeoJSON에서 지역명 속성을 찾지 못했습니다."
    )


# =========================================================
# 지도용 데이터 생성
# =========================================================

@st.cache_data(ttl=60 * 60 * 12)
def prepare_data():

    population_raw = download_population()
    geojson = download_geojson()

    population = make_population_table(
        population_raw
    )

    # 시군구 이름 생성
    population["시군구"] = population[
        "행정구역"
    ].apply(make_sigungu_name)

    # 전국/시도/읍면동 등이 섞여 있을 수 있으므로
    # 실제 시군구 형태만 남김
    population = population[
        population["시군구"].str.contains(
            "시|군|구",
            regex=True,
            na=False
        )
    ].copy()

    # 동일 시군구가 여러 번 존재하면 합계 재계산
    grouped = (
        population
        .groupby("시군구", as_index=False)
        .agg({
            "전체인구": "sum",
            "65세이상": "sum"
        })
    )

    grouped["고령인구비율"] = (
        grouped["65세이상"]
        / grouped["전체인구"]
        * 100
    )

    geo_name_col = find_geo_name_property(
        geojson
    )

    # GeoJSON 지역명 정리
    geo_names = []

    for feature in geojson["features"]:
        properties = feature.get("properties", {})
        name = properties.get(
            geo_name_col,
            ""
        )

        geo_names.append(
            normalize_name(name)
        )

    # GeoJSON에 지도용 이름 추가
    for i, feature in enumerate(
        geojson["features"]
    ):
        feature.setdefault(
            "properties", {}
        )

        feature["properties"]["map_name"] = (
            geo_names[i]
        )

    # 이름 매칭
    population_dict = {}

    for _, row in grouped.iterrows():

        name = normalize_name(
            row["시군구"]
        )

        population_dict[name] = {
            "전체인구": row["전체인구"],
            "65세이상": row["65세이상"],
            "고령인구비율": row["고령인구비율"]
        }

    # GeoJSON feature에 통계값 삽입
    matched = 0

    for feature in geojson["features"]:

        props = feature["properties"]

        name = props.get(
            "map_name",
            ""
        )

        info = population_dict.get(name)

        if info is not None:

            props["전체인구"] = info["전체인구"]
            props["65세이상"] = info["65세이상"]
            props["고령인구비율"] = info[
                "고령인구비율"
            ]

            matched += 1

        else:

            props["전체인구"] = None
            props["65세이상"] = None
            props["고령인구비율"] = None

    return geojson, grouped, matched


# =========================================================
# 색상
# =========================================================

def get_color(value):

    if value is None:
        return "#E5E7EB"

    try:
        value = float(value)
    except:
        return "#E5E7EB"

    # 단계구분도
    if value < 10:
        return "#FFF7BC"

    elif value < 15:
        return "#FEC44F"

    elif value < 20:
        return "#FE9929"

    elif value < 25:
        return "#EC7014"

    elif value < 30:
        return "#CC4C02"

    else:
        return "#993404"


# =========================================================
# SVG 지도 생성
# =========================================================

def geojson_to_svg(geojson):

    # 단순한 대한민국 지도 표시용
    # 실제 GeoJSON 좌표를 SVG 좌표로 변환

    features = geojson["features"]

    all_points = []

    def collect_coords(coords):

        if isinstance(coords, list):

            if (
                len(coords) >= 2
                and isinstance(coords[0], (int, float))
                and isinstance(coords[1], (int, float))
            ):
                all_points.append(
                    (
                        float(coords[0]),
                        float(coords[1])
                    )
                )

            else:

                for child in coords:
                    collect_coords(child)

    for feature in features:

        geometry = feature.get("geometry")

        if geometry:
            collect_coords(
                geometry.get("coordinates", [])
            )

    if not all_points:
        raise RuntimeError(
            "지도 좌표를 찾지 못했습니다."
        )

    min_lon = min(
        point[0] for point in all_points
    )
    max_lon = max(
        point[0] for point in all_points
    )
    min_lat = min(
        point[1] for point in all_points
    )
    max_lat = max(
        point[1] for point in all_points
    )

    width = 1000
    height = 750
    padding = 30

    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat

    scale_x = (
        (width - padding * 2)
        / lon_range
    )

    scale_y = (
        (height - padding * 2)
        / lat_range
    )

    scale = min(
        scale_x,
        scale_y
    )

    def project(lon, lat):

        x = (
            padding
            + (lon - min_lon) * scale
        )

        y = (
            height
            - padding
            - (lat - min_lat) * scale
        )

        return x, y

    def coords_to_path(coords):

        if not coords:
            return ""

        parts = []

        for i, coord in enumerate(coords):

            x, y = project(
                coord[0],
                coord[1]
            )

            if i == 0:
                parts.append(
                    f"M{x:.2f},{y:.2f}"
                )
            else:
                parts.append(
                    f"L{x:.2f},{y:.2f}"
                )

        parts.append("Z")

        return "".join(parts)

    def geometry_to_paths(geometry):

        if geometry is None:
            return []

        geometry_type = geometry.get(
            "type"
        )

        coordinates = geometry.get(
            "coordinates"
        )

        paths = []

        if geometry_type == "Polygon":

            for polygon in coordinates:

                paths.append(
                    coords_to_path(polygon)
                )

        elif geometry_type == "MultiPolygon":

            for multi_polygon in coordinates:

                for polygon in multi_polygon:

                    paths.append(
                        coords_to_path(polygon)
                    )

        return paths

    svg_parts = [
        f'''
        <svg
            viewBox="0 0 {width} {height}"
            width="100%"
            xmlns="http://www.w3.org/2000/svg"
            style="
                background:#F8FAFC;
                border-radius:16px;
                border:1px solid #E5E7EB;
            "
        >
        '''
    ]

    for feature in features:

        props = feature.get(
            "properties",
            {}
        )

        name = props.get(
            "map_name",
            "알 수 없음"
        )

        value = props.get(
            "고령인구비율"
        )

        color = get_color(value)

        paths = geometry_to_paths(
            feature.get("geometry")
        )

        if not paths:
            continue

        for path in paths:

            if value is None:
                tooltip = (
                    f"{name}: 데이터 없음"
                )
            else:
                tooltip = (
                    f"{name}: "
                    f"{float(value):.1f}%"
                )

            safe_tooltip = (
                tooltip
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

            svg_parts.append(
                f'''
                <path
                    d="{path}"
                    fill="{color}"
                    stroke="#FFFFFF"
                    stroke-width="0.7"
                    style="
                        cursor:pointer;
                        transition:opacity 0.15s;
                    "
                >
                    <title>{safe_tooltip}</title>
                </path>
                '''
            )

    svg_parts.append("</svg>")

    return "".join(svg_parts)


# =========================================================
# 실행
# =========================================================

try:

    with st.spinner(
        "전국 시군구 데이터를 불러오는 중..."
    ):

        geojson, population, matched = (
            prepare_data()
        )

except Exception as e:

    st.error(
        "데이터를 불러오는 과정에서 문제가 발생했습니다."
    )

    st.code(str(e))

    st.info(
        "인터넷 연결 또는 공개 데이터 주소가 "
        "변경되었는지 확인해주세요."
    )

    st.stop()


# =========================================================
# 사이드바
# =========================================================

st.sidebar.header("🎨 지도 설정")

min_value = st.sidebar.slider(
    "최소 비율",
    min_value=0.0,
    max_value=40.0,
    value=0.0,
    step=1.0
)

st.sidebar.markdown("---")

st.sidebar.markdown(
    """
### 색상 범례

🟨 **10% 미만**

🟧 **10~15%**

🟠 **15~20%**

🟤 **20~25%**

🔴 **25~30%**

🟥 **30% 이상**

⬜ **데이터 없음**
"""
)

st.sidebar.markdown("---")

st.sidebar.caption(
    "자료: 행정안전부 주민등록 인구통계"
)

# =========================================================
# 필터링
# =========================================================

filtered_geojson = json.loads(
    json.dumps(geojson)
)

for feature in filtered_geojson["features"]:

    value = feature["properties"].get(
        "고령인구비율"
    )

    if (
        value is not None
        and value < min_value
    ):
        # 필터 조건에 맞지 않는 지역은 회색 처리
        feature["properties"][
            "고령인구비율"
        ] = None


# =========================================================
# 지도
# =========================================================

st.subheader("전국 시군구별 고령인구 비율")

svg = geojson_to_svg(
    filtered_geojson
)

st.components.v1.html(
    svg,
    height=780,
    scrolling=False
)

# =========================================================
# 설명
# =========================================================

st.markdown(
    """
### 📌 지도 읽는 법

각 시군구의 **전체 인구 중 만 65세 이상 인구가 차지하는 비율**을
단계별 색상으로 표현했습니다.

색이 진할수록 해당 지역의 고령인구 비율이 높습니다.

마우스를 지도 위 지역에 올리면 해당 시군구의
고령인구 비율을 확인할 수 있습니다.
"""
)

# =========================================================
# 통계 요약
# =========================================================

st.subheader("📊 전국 시군구 통계")

valid = population[
    population["고령인구비율"].notna()
].copy()

if len(valid) > 0:

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "분석 시군구",
            f"{len(valid):,}곳"
        )

    with col2:
        st.metric(
            "평균 고령인구 비율",
            f"{valid['고령인구비율'].mean():.1f}%"
        )

    with col3:
        st.metric(
            "최고 고령인구 비율",
            f"{valid['고령인구비율'].max():.1f}%"
        )

    with col4:
        st.metric(
            "최저 고령인구 비율",
            f"{valid['고령인구비율'].min():.1f}%"
        )

# =========================================================
# 검색
# =========================================================

st.subheader("🔎 시군구 검색")

search = st.text_input(
    "시군구 이름을 입력하세요",
    placeholder="예: 세종, 강릉, 종로구"
)

if search:

    result = valid[
        valid["시군구"].str.contains(
            search,
            case=False,
            na=False
        )
    ].copy()

    if len(result) == 0:

        st.warning(
            "검색 결과가 없습니다."
        )

    else:

        result = result.sort_values(
            "고령인구비율",
            ascending=False
        )

        result["전체인구"] = (
            result["전체인구"]
            .round()
            .astype(int)
        )

        result["65세이상"] = (
            result["65세이상"]
            .round()
            .astype(int)
        )

        result["고령인구비율"] = (
            result["고령인구비율"]
            .round(2)
        )

        st.dataframe(
            result[
                [
                    "시군구",
                    "전체인구",
                    "65세이상",
                    "고령인구비율"
                ]
            ],
            use_container_width=True,
            hide_index=True
        )

# =========================================================
# 전체 데이터
# =========================================================

with st.expander("📋 전체 시군구 데이터 보기"):

    table = valid.copy()

    table["전체인구"] = (
        table["전체인구"]
        .round()
        .astype(int)
    )

    table["65세이상"] = (
        table["65세이상"]
        .round()
        .astype(int)
    )

    table["고령인구비율"] = (
        table["고령인구비율"]
        .round(2)
    )

    table = table.sort_values(
        "고령인구비율",
        ascending=False
    )

    st.dataframe(
        table[
            [
                "시군구",
                "전체인구",
                "65세이상",
                "고령인구비율"
            ]
        ],
        use_container_width=True,
        hide_index=True
    )

# =========================================================
# 데이터 출처
# =========================================================

st.markdown("---")

st.caption(
    "행정구역 경계: 통계청 SGIS 기반 공개 행정경계 데이터"
)

st.caption(
    "인구 데이터: 행정안전부 주민등록 연령별 인구현황"
)

st.caption(
    "고령인구비율 = 만 65세 이상 인구 ÷ 전체 인구 × 100"
)
