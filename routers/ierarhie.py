from fastapi import APIRouter, Path, Request, HTTPException
from pydantic import BaseModel
from auth import get_db, limiter, _dynamic_limit, cached_json
from routers.caen import CAENEntry, _QUERY_BASE

router = APIRouter(tags=["Ierarhie"])


class Sectiune(BaseModel):
    cod: str
    denumire: str


class Diviziune(BaseModel):
    cod: str
    denumire: str
    sectiune_cod: str


class Grupa(BaseModel):
    cod: str
    denumire: str
    diviziune_cod: str


@router.get(
    "/sectiuni",
    response_model=list[Sectiune],
    summary="Listeaza toate sectiunile CAEN",
)
@limiter.limit(_dynamic_limit)
def list_sectiuni(request: Request):
    """Returneaza lista tuturor sectiunilor CAEN Rev. 3, ordonate dupa cod."""
    with get_db() as conn:
        rows = conn.execute("SELECT cod, denumire FROM sectiuni ORDER BY cod").fetchall()
    return cached_json(request, [dict(r) for r in rows])


@router.get(
    "/sectiuni/{cod}",
    response_model=Sectiune,
    summary="Detalii sectiune dupa cod",
)
@limiter.limit(_dynamic_limit)
def get_sectiune(
    request: Request,
    cod: str = Path(pattern=r"^[A-Za-z]{1,2}$", description="Cod sectiune (litera, ex: A)"),
):
    """Returneaza denumirea sectiunii identificate prin codul dat."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT cod, denumire FROM sectiuni WHERE cod = ?", (cod.upper(),)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sectiunea '{cod}' nu a fost gasita.")
    return cached_json(request, dict(row))


@router.get(
    "/sectiuni/{cod}/diviziuni",
    response_model=list[Diviziune],
    summary="Diviziunile unei sectiuni",
)
@limiter.limit(_dynamic_limit)
def list_diviziuni_by_sectiune(
    request: Request,
    cod: str = Path(pattern=r"^[A-Za-z]{1,2}$", description="Cod sectiune (litera, ex: A)"),
):
    """Returneaza toate diviziunile din sectiunea specificata."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sectiuni WHERE cod = ?", (cod.upper(),)).fetchone():
            raise HTTPException(status_code=404, detail=f"Sectiunea '{cod}' nu a fost gasita.")
        rows = conn.execute(
            "SELECT cod, denumire, sectiune_cod FROM diviziuni WHERE sectiune_cod = ? ORDER BY cod",
            (cod.upper(),),
        ).fetchall()
    return cached_json(request, [dict(r) for r in rows])


@router.get(
    "/diviziuni/{cod}",
    response_model=Diviziune,
    summary="Detalii diviziune dupa cod",
)
@limiter.limit(_dynamic_limit)
def get_diviziune(
    request: Request,
    cod: str = Path(pattern=r"^\d{2}$", description="Cod diviziune (2 cifre, ex: 01)"),
):
    """Returneaza denumirea si sectiunea parinte a diviziunii."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT cod, denumire, sectiune_cod FROM diviziuni WHERE cod = ?", (cod,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Diviziunea '{cod}' nu a fost gasita.")
    return cached_json(request, dict(row))


@router.get(
    "/diviziuni/{cod}/grupe",
    response_model=list[Grupa],
    summary="Grupele unei diviziuni",
)
@limiter.limit(_dynamic_limit)
def list_grupe_by_diviziune(
    request: Request,
    cod: str = Path(pattern=r"^\d{2}$", description="Cod diviziune (2 cifre, ex: 01)"),
):
    """Returneaza toate grupele din diviziunea specificata."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM diviziuni WHERE cod = ?", (cod,)).fetchone():
            raise HTTPException(status_code=404, detail=f"Diviziunea '{cod}' nu a fost gasita.")
        rows = conn.execute(
            "SELECT cod, denumire, diviziune_cod FROM grupe WHERE diviziune_cod = ? ORDER BY cod",
            (cod,),
        ).fetchall()
    return cached_json(request, [dict(r) for r in rows])


@router.get(
    "/grupe/{cod}",
    response_model=Grupa,
    summary="Detalii grupa dupa cod",
)
@limiter.limit(_dynamic_limit)
def get_grupa(
    request: Request,
    cod: str = Path(pattern=r"^\d{3}$", description="Cod grupa (3 cifre, ex: 011)"),
):
    """Returneaza denumirea si diviziunea parinte a grupei."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT cod, denumire, diviziune_cod FROM grupe WHERE cod = ?", (cod,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Grupa '{cod}' nu a fost gasita.")
    return cached_json(request, dict(row))


@router.get(
    "/grupe/{cod}/clase",
    response_model=list[CAENEntry],
    summary="Clasele (coduri CAEN) dintr-o grupa",
)
@limiter.limit(_dynamic_limit)
def list_clase_by_grupa(
    request: Request,
    cod: str = Path(pattern=r"^\d{3}$", description="Cod grupa (3 cifre, ex: 011)"),
):
    """Returneaza toate clasele CAEN din grupa specificata, cu detalii complete."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM grupe WHERE cod = ?", (cod,)).fetchone():
            raise HTTPException(status_code=404, detail=f"Grupa '{cod}' nu a fost gasita.")
        rows = conn.execute(
            _QUERY_BASE + " WHERE g.cod = ? ORDER BY c.cod", (cod,)
        ).fetchall()
    return cached_json(request, [dict(r) for r in rows])
