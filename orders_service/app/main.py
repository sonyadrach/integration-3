import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
from typing import List
import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status, BackgroundTasks, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from . import crud, models, schemas
from .database import Base, engine, get_db, SessionLocal

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("orders-api")

FORECAST_URL = os.getenv(
    "FORECAST_URL",
    "http://forecast-api:8001",
)

FORECAST_TIMEOUT = float(
    os.getenv("FORECAST_TIMEOUT", "3.0")
)

ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)

BATCH_MAX_ITEMS = int(
    os.getenv("BATCH_MAX_ITEMS", "50")
)
FORECAST_FEATURE_KEYS = {
    "gr_liv_area",
    "total_bsmt_sf",
    "garage_area",
    "year_built",
    "overall_qual",
    "ms_zoning",
}

ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=FORECAST_TIMEOUT)
    yield
    await http_client.aclose()

Base.metadata.create_all(bind=engine)

def get_jwt_secret() -> str:
    """Read the JWT signing key exclusively from the Docker secret file."""
    secret_path = Path("/run/secrets/jwt_secret_key")
    if not secret_path.is_file():
        raise RuntimeError(f"Required secret 'jwt_secret_key' is missing at {secret_path}")
    return secret_path.read_text(encoding="utf-8").strip()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify raw password against bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Generate bcrypt hash for password."""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT token containing subject, issued-at, and expiration claims."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"iat": now, "exp": expire})
    return jwt.encode(to_encode, get_jwt_secret(), algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    """Validate Bearer JWT token, verify user existence, and check active status."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Валідація токена з явною вимогою алгоритму HS256 проти algorithm confusion / alg: none
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception

    return user

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Orders API",
    version="0.2.0",
    lifespan=lifespan,
)

@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(0.2),
    retry=retry_if_exception_type(httpx.TimeoutException),
    reraise=True,
)
def _request_prediction(payload: dict) -> float:
    """Request a price prediction from the forecasting service."""
    model_payload = {k: v for k, v in payload.items() if k in FORECAST_FEATURE_KEYS}

    with httpx.Client(timeout=FORECAST_TIMEOUT) as client:
        response = client.post(
            f"{FORECAST_URL}/predict",
            json=model_payload,
        )
        response.raise_for_status()
        return float(response.json()["predicted_price"])

async def _request_prediction_async(payload: dict) -> float:
    """Async request a price prediction from the forecasting service."""
    model_payload = {k: v for k, v in payload.items() if k in FORECAST_FEATURE_KEYS}
    
    response = await http_client.post(
        f"{FORECAST_URL}/predict",
        json=model_payload,
    )
    response.raise_for_status()
    return float(response.json()["predicted_price"])

async def process_order_forecast_background(order_id: int):
    """Фонова задача для отримання прогнозу."""
    db: Session = SessionLocal() 
    try:
        order = db.query(models.Order).filter(models.Order.id == order_id).first()
        if not order:
            logger.warning("Order %s was deleted before background processing started.", order_id)
            return

        features = {k: getattr(order, k) for k in FORECAST_FEATURE_KEYS}
        
        try:
            price = await _request_prediction_async(features)
            
            order.predicted_price = price
            order.status = "priced"
            
            logger.info("Order %s forecast success: %s USD at %s", order_id, price, datetime.now(timezone.utc).isoformat())
            
        except Exception as exc:
            order.status = "failed"
            logger.error("Order %s forecast failed: %s at %s", order_id, exc, datetime.now(timezone.utc).isoformat())
        
        db.commit()
    finally:
        db.close()
# --- Public Endpoints ---

@app.get("/health")
def health() -> dict[str, str]:
    """Return the health status of the Orders API (Unauthenticated)."""
    return {"status": "ok"}


# --- Authentication Endpoints (R2.4) ---

@app.post(
    "/auth/register",
    response_model=schemas.UserOut,
    status_code=status.HTTP_201_CREATED,
)
def register(
    user_in: schemas.UserCreate,
    db: Session = Depends(get_db),
):
    """Register a new user."""
    existing_user = db.query(models.User).filter(models.User.username == user_in.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    user = models.User(
        username=user_in.username,
        hashed_password=get_password_hash(user_in.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post(
    "/auth/login",
    response_model=schemas.Token,
)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 password flow login yielding a Bearer JWT."""
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get(
    "/auth/me",
    response_model=schemas.UserOut,
)
def read_current_user(
    current_user: models.User = Depends(get_current_user),
):
    """Retrieve details of the currently authenticated user."""
    return current_user


# --- Authenticated Orders Endpoints (R2.6) ---

@app.post(
    "/orders",
    response_model=schemas.OrderRead,
    status_code=status.HTTP_202_ACCEPTED, 
)
async def create_order(
    data: schemas.OrderCreate,
    background_tasks: BackgroundTasks, 
    response: Response,                
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create an order and schedule forecast calculation in the background."""
    
    new_order = crud.create_order(
        db,
        data,
        price=None, 
    )
    background_tasks.add_task(process_order_forecast_background, new_order.id)
    response.headers["Location"] = f"/orders/{new_order.id}"
    
    return new_order

@app.post(
    "/orders/batch",
    response_model=schemas.BatchOrderResponse,
    status_code=status.HTTP_200_OK,
)
async def create_orders_batch(
    orders_in: List[schemas.OrderCreate],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a batch of orders and fetch predictions concurrently."""
    if len(orders_in) > BATCH_MAX_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum batch size is {BATCH_MAX_ITEMS}",
        )

    db_orders = []
    for order_data in orders_in:
        new_order = crud.create_order(db, order_data, price=None)
        db_orders.append(new_order)

    tasks = []
    for order_data in orders_in:
        tasks.append(_request_prediction_async(order_data.model_dump()))

    forecast_results = await asyncio.gather(*tasks, return_exceptions=True)

    results_out = []
    for idx, (order_db, forecast_res) in enumerate(zip(db_orders, forecast_results)):
        result_item = schemas.BatchOrderResult(
            index=idx, 
            order_id=order_db.id, 
            status=""
        )
        
        if isinstance(forecast_res, Exception):
            logger.warning(f"Batch prediction failed for order {order_db.id}: {forecast_res}")
            result_item.status = "failed"
            result_item.error = str(forecast_res)
        else:
            order_db.predicted_price = forecast_res
            result_item.status = "priced"
            result_item.predicted_price = forecast_res
            
        results_out.append(result_item)

    db.commit()

    return schemas.BatchOrderResponse(
        total=len(orders_in),
        results=results_out
    )


@app.get(
    "/orders",
    response_model=list[schemas.OrderRead],
)
def list_orders(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return a paginated list of orders."""
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="Limit must be between 1 and 100.",
        )

    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail="Offset must be non-negative.",
        )

    return crud.list_orders(
        db,
        limit,
        offset,
    )


@app.get(
    "/orders/{oid}",
    response_model=schemas.OrderRead,
)
def get_order(
    oid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return a single order by ID."""
    order = crud.get_order(db, oid)

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    return order


@app.patch(
    "/orders/{oid}",
    response_model=schemas.OrderRead,
)
def update_order(
    oid: int,
    data: schemas.OrderUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Update an existing order and recalculate price if property features change."""
    order = crud.get_order(db, oid)

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    update_dict = data.model_dump(exclude_unset=True)
    if not update_dict:
        return order

    order = crud.update_order(db, order, data)

    has_property_changes = any(field in FORECAST_FEATURE_KEYS for field in update_dict)

    if has_property_changes:
        order.status = "pending"
        
        order.predicted_price = None
        
        db.commit()
        db.refresh(order)

        background_tasks.add_task(process_order_forecast_background, order.id)

    return order


@app.delete(
    "/orders/{oid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_order(
    oid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    """Delete an existing order."""
    order = crud.get_order(db, oid)

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    crud.delete_order(db, order)

