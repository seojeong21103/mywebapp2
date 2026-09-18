import streamlit as st
import pandas as pd
import json
import urllib.request
import re

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

# 2026년 7월 1일 기준 행정구역 경계
GEOJSON_URL = (
    "https://raw.githubusercontent.com/"
    "vuski/admdongkor/master/"
    "ver20260701/HangJeongDong_ver20260701.geojson"
)

CSV_FILE = "population.csv"


# =========================================================
# 행정구역 경계 불러오기
# =========================================================

@st.cache_data(ttl=86400)
def load_geojson():

    with urllib.request.urlopen(
        GEOJSON_URL,
        timeout=60
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


# =========================================================
# CSV 불러오기
# =========================================================

@st.cache_data
def load_population():

    encodings = [
        "cp949",
        "euc-kr",
        "utf-8-sig",
        "utf-8"
    ]

    last_error = None

    for encoding in encodings:

        try:

            return pd.read_csv(
                CSV_FILE,
                encoding=encoding
            )

        except Exception as e:

            last_error = e

    raise last_error


# =========================================================
# 숫자 변환
# =========================================================

def to_number(value):

    if pd.isna(value):
        return 0

    text = str(value)

    text = (
        text
        .replace(",", "")
        .replace('"', "")
        .strip()
    )

    try:
        return float(text)

    except:
        return 0


# =========================================================
# 행정구역명 정리
# =========================================================

def clean_area_name(name):

    name = str(name)

    # 괄호 제거
    name = re.sub(
        r"\s*\([^)]*\)",
        "",
        name
    )

    # 앞뒤 공백 제거
    name = name.strip()

    return name


# =========================================================
# CSV에서 65세 이상 계산
# =========================================================

def make_population_data(df):

    # 행정구역 컬럼 찾기
    area_col = None

    for col in df.columns:

        if "행정구역" in str(col):

            area_col = col
            break

    if area_col is None:

        area_col = df.columns[0]


    # "계" 성별의 연령별 컬럼 찾기
    age_columns = []

    for col in df.columns:

        text = str(col)

        # 계_65세
        # 202606_계_65세
        if "계" not in text:
            continue

        match = re.search(
            r"(\d+)세",
            text
        )

        if match:

            age = int(
                match.group(1)
            )

            if age >= 65:

                age_columns.append(col)


    if len(age_columns) == 0:

        raise ValueError(
            "65세 이상 연령 컬럼을 찾지 못했습니다."
        )


    # 전체 인구
    total_col = None

    for col in df.columns:

        text = str(col)

        if (
            "계" in text
            and (
                "0세" in text
                or "총인구" in text
                or "총계" in text
            )
        ):
            total_col = col
            break


    # 전체 인구 컬럼을 찾지 못하면
    # 0~100세 이상 계 컬럼을 합산
    age_all_columns = []

    for col in df.columns:

        text = str(col)

        if "계" not in text:
            continue

        match = re.search(
            r"(\d+)세",
            text
        )

        if match:

            age = int(
                match.group(1)
            )

            if 0 <= age <= 99:

                age_all_columns.append(col)

        elif "100세 이상" in text:

            age_all_columns.append(col)


    result = pd.DataFrame()

    result["행정구역"] = (
        df[area_col]
        .astype(str)
        .apply(clean_area_name)
    )


    # 전체 인구 계산
    result["전체인구"] = 0.0

    for col in age_all_columns:

        result["전체인구"] += (
            df[col]
            .apply(to_number)
        )


    # 65세 이상 계산
    result["65세이상"] = 0.0

    for col in age_columns:

        result["65세이상"] += (
            df[col]
            .apply(to_number)
        )


    # 비율
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
# 시군구 이름 만들기
# =========================================================

def make_sigungu_name(name):

    name = clean_area_name(name)

    parts = name.split()

    if len(parts) < 2:

        return name


    # 특별시 / 광역시 / 특별자치시
    if (
        parts[0].endswith("특별시")
        or parts[0].endswith("광역시")
        or parts[0].endswith("특별자치시")
    ):

        return " ".join(parts[:2])


    # 도
    if parts[0].endswith("도"):

        # 수원시 영통구
        if (
            len(parts) >= 3
            and parts[2].endswith("구")
        ):

            return " ".join(parts[:3])

        # 일반 시/군
        return " ".join(parts[:2])


    return name


# =========================================================
# 데이터 준비
# =========================================================

@st.cache_data(ttl=86400)
def prepare_data():

    df = load_population()

    population = make_population_data(df)

    population["시군구"] = (
        population["행정구역"]
        .apply(make_sigungu_name)
    )


    # 읍면동 데이터가 여러 개 존재하므로
    # 시군구별로 다시 합산
    grouped = (
        population
        .groupby("시군구")
        .agg(
            전체인구=("전체인구", "sum"),
            65세이상=("65세이상", "sum")
        )
        .reset_index()
    )


    grouped["고령인구비율"] = (
        grouped["65세이상"]
        / grouped["전체인구"]
        * 100
    )


    geojson = load_geojson()


    # 행정동 GeoJSON을 시군구 단위로 사용하기 위해
    # 시군구 코드 기준으로 묶기
    for feature in geojson["features"]:

        properties = feature.get(
            "properties",
            {}
        )

        # 여러 가능한 이름 필드 확인
        name = (
            properties.get("sggnm")
            or properties.get("sgg_nm")
            or properties.get("SIG_KOR_NM")
            or properties.get("adm_nm")
            or ""
        )

        properties["map_name"] = (
            clean_area_name(name)
        )

        feature["properties"] = properties


    # GeoJSON에 해당하는 시군구 이름이 있는지 확인
    names = set(
        grouped["시군구"]
    )


    # 지도 데이터에 통계값 연결
    for feature in geojson["features"]:

        props = feature["properties"]

        name = props.get(
            "map_name",
            ""
        )

        row = grouped[
            grouped["시군구"] == name
        ]

        if len(row) > 0:

            data = row.iloc[0]

            props["전체인구"] = float(
                data["전체인구"]
            )

            props["65세이상"] = float(
                data["65세이상"]
            )

            props["고령인구비율"] = float(
                data["고령인구비율"]
            )

        else:

            props["전체인구"] = None
            props["65세이상"] = None
            props["고령인구비율"] = None


    return geojson, grouped


# =========================================================
# 색상
# =========================================================

def get_color(value):

    if value is None:
        return "#E5E7EB"

    if value < 10:
        return "#FFF7BC"

    if value < 15:
        return "#FEC44F"

    if value < 20:
        return "#FE9929"

    if value < 25:
        return "#EC7014"

    if value < 30:
        return "#CC4C02"

    return "#993404"


# =========================================================
# 지도용 SVG
# =========================================================

def create_svg(geojson):

    points = []


    def collect(coords):

        if not isinstance(coords, list):
            return

        if (
            len(coords) >= 2
            and isinstance(coords[0], (int, float))
            and isinstance(coords[1], (int, float))
        ):

            points.append(
                (
                    coords[0],
                    coords[1]
                )
            )

        else:

            for item in coords:

                collect(item)


    for feature in geojson["features"]:

        geometry = feature.get(
            "geometry"
        )

        if geometry:

            collect(
                geometry.get(
                    "coordinates",
                    []
                )
            )


    if not points:

        raise ValueError(
            "지도 좌표를 찾을 수 없습니다."
        )


    min_x = min(
        p[0] for p in points
    )

    max_x = max(
        p[0] for p in points
    )

    min_y = min(
        p[1] for p in points
    )

    max_y = max(
        p[1] for p in points
    )


    width = 1000
    height = 760
    padding = 30


    scale_x = (
        (width - padding * 2)
        / (max_x - min_x)
    )

    scale_y = (
        (height - padding * 2)
        / (max_y - min_y)
    )

    scale = min(
        scale_x,
        scale_y
    )


    def project(x, y):

        px = (
            padding
            + (x - min_x)
            * scale
        )

        py = (
            height
            - padding
            - (y - min_y)
            * scale
        )

        return px, py


    def polygon_path(coords):

        if not coords:
            return ""

        path = []

        for i, point in enumerate(coords):

            x, y = project(
                point[0],
                point[1]
            )

            if i == 0:

                path.append(
                    f"M{x:.2f},{y:.2f}"
                )

            else:

                path.append(
                    f"L{x:.2f},{y:.2f}"
                )

        path.append("Z")

        return "".join(path)


    def geometry_paths(geometry):

        if not geometry:
            return []

        gtype = geometry.get(
            "type"
        )

        coords = geometry.get(
            "coordinates"
        )

        paths = []

        if gtype == "Polygon":

            for polygon in coords:

                paths.append(
                    polygon_path(polygon)
                )

        elif gtype == "MultiPolygon":

            for multipolygon in coords:

                for polygon in multipolygon:

                    paths.append(
                        polygon_path(polygon)
                    )

        return paths


    svg = f"""
    <svg
        viewBox="0 0 {width} {height}"
        width="100%"
        xmlns="http://www.w3.org/2000/svg"
        style="
            background:#f8fafc;
            border:1px solid #e5e7eb;
            border-radius:16px;
        "
    >
    """


    for feature in geojson["features"]:

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


        if value is None:

            tooltip = (
                f"{name}: 데이터 없음"
            )

        else:

            tooltip = (
                f"{name}: "
                f"{value:.1f}%"
            )


        geometry = feature.get(
            "geometry"
        )


        for path in geometry_paths(
            geometry
        ):

            svg += f"""
            <path
                d="{path}"
                fill="{color}"
                stroke="#ffffff"
                stroke-width="0.5"
            >
                <title>
                    {tooltip}
                </title>
            </path>
            """


    svg += "</svg>"

    return svg


# =========================================================
# 앱 실행
# =========================================================

try:

    with st.spinner(
        "전국 고령화 데이터를 불러오는 중..."
    ):

        geojson, population = (
            prepare_data()
        )

except Exception as e:

    st.error(
        "데이터를 불러오지 못했습니다."
    )

    st.code(
        str(e)
    )

    st.info(
        "GitHub 저장소에 "
        "`population.csv` 파일이 있는지 확인해주세요."
    )

    st.stop()


# =========================================================
# 사이드바
# =========================================================

st.sidebar.header(
    "🎨 지도 설정"
)

st.sidebar.markdown(
    """
**고령인구 비율**

🟨 10% 미만

🟧 10~15%

🟠 15~20%

🟤 20~25%

🔴 25~30%

🟥 30% 이상
"""
)


# =========================================================
# 지도
# =========================================================

st.subheader(
    "📍 시군구별 65세 이상 인구 비율"
)

svg = create_svg(
    geojson
)

st.components.v1.html(
    svg,
    height=780,
    scrolling=False
)


# =========================================================
# 통계
# =========================================================

valid = population[
    population["고령인구비율"].notna()
].copy()


col1, col2, col3 = st.columns(3)


with col1:

    st.metric(
        "시군구 수",
        f"{len(valid):,}"
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


# =========================================================
# 검색
# =========================================================

st.subheader(
    "🔎 시군구 검색"
)

search = st.text_input(
    "지역명을 입력하세요",
    placeholder="예: 세종특별자치시, 종로구, 강릉시"
)


if search:

    result = valid[
        valid["시군구"].str.contains(
            search,
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

with st.expander(
    "📊 전체 시군구 데이터"
):

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


st.markdown("---")

st.caption(
    "인구 자료: 행정안전부 주민등록 연령별 인구현황"
)

st.caption(
    "행정구역 경계: 통계청 SGIS 기반 "
    "vuski/admdongkor"
)
