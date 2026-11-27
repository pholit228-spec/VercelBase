import io, os, re, time, uuid, asyncio, base64, gzip
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile
from pg_backend import connect

FEE=75; SLOTS=9
TRADE={"trade","трейд","обмен","размен"}
ACCEPT={"confirm","подтвердить","принять"}
YES={"yes","да"}; NO={"no","нет"}
ADD={"add","добавить","новый"}; REMOVE={"remove","убрать","удалить"}; CLEAR={"clear","очистить"}
_READY=False

def schema():
    global _READY
    if _READY:return
    c=connect()
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS trade_sessions(
            id TEXT PRIMARY KEY,chat_id BIGINT NOT NULL,initiator_id BIGINT NOT NULL,
            target_id BIGINT NOT NULL,status TEXT NOT NULL,
            initiator_yes BOOLEAN NOT NULL DEFAULT FALSE,target_yes BOOLEAN NOT NULL DEFAULT FALSE,
            created_at DOUBLE PRECISION NOT NULL,updated_at DOUBLE PRECISION NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trade_items(
            session_id TEXT NOT NULL,user_id BIGINT NOT NULL,position INTEGER NOT NULL,
            slime_key TEXT NOT NULL,PRIMARY KEY(session_id,user_id,position))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trade_user ON trade_sessions(initiator_id,target_id,status)")
        c.commit(); _READY=True
    finally:c.close()

def cmd(text):
    p=(text or "").strip().split(maxsplit=1)
    if not p:return "",[]
    n=p[0].lstrip("/").split("@",1)[0].lower()
    return n,(p[1].strip().split() if len(p)>1 else [])

def active(uid):
    c=connect()
    try:return c.execute("""SELECT * FROM trade_sessions WHERE status IN ('pending','active','voting')
        AND (initiator_id=? OR target_id=?) ORDER BY created_at DESC LIMIT 1""",(uid,uid)).fetchone()
    finally:c.close()

def session(sid):
    c=connect()
    try:return c.execute("SELECT * FROM trade_sessions WHERE id=?",(sid,)).fetchone()
    finally:c.close()

def items(sid,uid):
    c=connect()
    try:return c.execute("""SELECT position,slime_key FROM trade_items
        WHERE session_id=? AND user_id=? ORDER BY position""",(sid,uid)).fetchall()
    finally:c.close()

def opponent(r,uid):return int(r["target_id"] if int(r["initiator_id"])==uid else r["initiator_id"])

def user(legacy,raw):
    raw=raw.lstrip("@")
    try:r=legacy.find_user_by_username(raw)
    except Exception:r=None
    return (int(r[0]),str(r[1])) if r else None

def slime(legacy,name):
    try:
        s=legacy.load_slimes(); k=legacy.find_slime_key_by_name(name,s)
        return (k,s[k]) if k in s else None
    except Exception:return None

def owned(legacy,uid,key):
    try:return int(legacy.get_collection(uid).get(key,0))
    except Exception:return False

def art(legacy,key):
    try:
        src=legacy.get_photo_source(legacy.SLIME_PACK_ASSETS.get(key))
        if not src or isinstance(src,str):return None
        b=src.read(); src.close(); return b
    except Exception:return None

_COMFORTAA_CACHE=None

def _comfortaa():
    global _COMFORTAA_CACHE
    if _COMFORTAA_CACHE and os.path.exists(_COMFORTAA_CACHE):
        return _COMFORTAA_CACHE
    root=os.path.join(os.path.dirname(__file__),"fonts")
    raw=[]
    for i in range(15):
        p=os.path.join(root,f"comfortaa_bold.part{i:02d}")
        if not os.path.exists(p):
            return None
        with open(p,"r",encoding="ascii") as f: raw.append(f.read().strip())
    out="/tmp/Comfortaa-Bold.ttf"
    try:
        with open(out,"wb") as f: f.write(gzip.decompress(base64.b64decode("".join(raw))))
        _COMFORTAA_CACHE=out
        return out
    except Exception:
        return None

def font(n,b=False):
    # Use bundled Comfortaa Bold for the Pillow trade cards.
    local=_comfortaa() if b else None
    fallback="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if b else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    p=local or fallback
    return ImageFont.truetype(p,n) if os.path.exists(p) else ImageFont.load_default()

def fit(data,size):
    if not data:return None
    try:
        im=Image.open(io.BytesIO(data)).convert("RGBA"); im.thumbnail(size,Image.Resampling.LANCZOS); return im
    except Exception:return None

async def avatar(bot,uid,size):
    try:
        p=await bot.get_user_profile_photos(user_id=uid,limit=1)
        if not p.photos:return None
        f=await bot.get_file(p.photos[0][-1].file_id); b=io.BytesIO(); await bot.download(f,destination=b)
        return fit(b.getvalue(),(size,size))
    except Exception:return None

async def render(legacy,bot,sid):
    r=session(sid); left,right=int(r["initiator_id"]),int(r["target_id"])
    c=connect()
    try:
        ns={}
        for uid in (left,right):
            u=c.execute("SELECT username,first_name FROM users WHERE user_id=?",(uid,)).fetchone()
            ns[uid]=("@"+str(u["username"]) if u and u["username"] else (str(u["first_name"]) if u else str(uid)))
    finally:c.close()
    W,H=1460,1060; im=Image.new("RGB",(W,H),"#c8c9d6"); d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,210),fill="#85879f")
    d.text((55,68),ns[left],font=font(42,True),fill="white")
    d.text((W-55,68),ns[right],font=font(42,True),fill="white",anchor="ra")
    av=await asyncio.gather(avatar(bot,left,130),avatar(bot,right,130))
    for x,a,uid in ((125,av[0],left),(W-125,av[1],right)):
        if a:
            m=Image.new("L",(130,130),0); ImageDraw.Draw(m).ellipse((0,0,129,129),fill=255); im.paste(a,(x-65,35),m)
        else:
            d.ellipse((x-65,35,x+65,165),fill="#f5f5f7"); d.text((x,100),ns[uid].lstrip("@")[:1].upper(),font=font(58,True),fill="#85879f",anchor="mm")
    d.rounded_rectangle((38,260,W-38,1000),radius=42,fill="#9698b2"); mid=W//2; d.rectangle((mid-38,212,mid+38,1000),fill="#f8f8fa")
    lw,lh,gx,gy=195,180,20,30
    for side,uid in enumerate((left,right)):
        its=items(sid,uid)
        for pos in range(9):
            col,row=pos%3,pos//3; x=(65 if side==0 else mid+75)+col*(lw+gx); y=300+row*(lh+gy)
            d.rounded_rectangle((x,y,x+lw,y+lh),radius=22,fill="#626477",outline="#74778e",width=4)
            it=next((z for z in its if int(z["position"])==pos),None)
            if it:
                a=fit(art(legacy,str(it["slime_key"])),(145,145))
                if a:im.paste(a,(x+(lw-a.width)//2,y+(lh-a.height)//2),a)
    state={"pending":"Ожидание принятия","active":"Добавляйте слаймов","voting":"Подтвердите /yes или /no","completed":"Завершён"}.get(str(r["status"]),str(r["status"]))
    d.text((mid,1025),f"Трейд • комиссия {FEE} баксов • {state}",font=font(28,True),fill="#66687d",anchor="mm")
    b=io.BytesIO(); im.save(b,"PNG",optimize=True); return b.getvalue()

async def board(legacy,msg,sid,bot,text):
    b=await render(legacy,bot,sid)
    await bot.send_photo(msg.chat.id,BufferedInputFile(b,filename="trade.png"),caption=text,reply_to_message_id=msg.message_id)

async def start(legacy,msg,bot,raw):
    me=int(msg.from_user.id); t=user(legacy,raw)
    if not t:await msg.answer("Не нашёл пользователя. Нужен его @username.");return
    tid,name=t
    if tid==me:await msg.answer("Нельзя начать трейд с самим собой.");return
    if active(me) or active(tid):await msg.answer("У одного из пользователей уже есть активный трейд.");return
    sid=uuid.uuid4().hex; now=time.time(); c=connect()
    try:
        c.execute("""INSERT INTO trade_sessions(id,chat_id,initiator_id,target_id,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",(sid,msg.chat.id,me,tid,"pending",now,now));c.commit()
    finally:c.close()
    await msg.answer(f"Трейд отправлен пользователю {name}. Ждём /confirm или /принять.")
    target_chat=msg.chat.id if msg.chat.type in {"group","supergroup"} else tid
    try:await bot.send_message(target_chat,f"Вам предлагают трейд. Команда: /confirm или /принять. Комиссия: {FEE} баксов с каждого.")
    except Exception:pass

async def accept(legacy,msg,bot):
    me=int(msg.from_user.id);r=active(me)
    if not r or str(r["status"])!="pending" or int(r["target_id"])!=me:
        await msg.answer("Для вас нет ожидающего трейда.");return
    c=connect()
    try:c.execute("UPDATE trade_sessions SET status='active',updated_at=? WHERE id=?",(time.time(),r["id"]));c.commit()
    finally:c.close()
    await board(legacy,msg,str(r["id"]),bot,"Трейд принят. Добавляйте слаймов: /add [название].")

async def add(legacy,msg,bot,name):
    me=int(msg.from_user.id);r=active(me)
    if not r or str(r["status"])!="active":await msg.answer("Сейчас у вас нет активного трейда.");return
    s=slime(legacy,name)
    if not s:await msg.answer("Не нашёл такого слайма.");return
    key,info=s; c=connect()
    try:
        n=int(c.execute("SELECT COUNT(*) AS n FROM trade_items WHERE session_id=? AND user_id=?",(r["id"],me)).fetchone()["n"])
        if n>=SLOTS:await msg.answer("В трейде максимум 9 слаймов.");return
        used=int(c.execute("SELECT COUNT(*) AS n FROM trade_items WHERE session_id=? AND user_id=? AND slime_key=?",(r["id"],me,key)).fetchone()["n"])
        if not owned(legacy,me,key) or used>=int(legacy.get_collection(me).get(key,0)):
            await msg.answer("У вас нет свободного экземпляра этого слайма.");return
        for pos in range(SLOTS):
            if not c.execute("SELECT 1 FROM trade_items WHERE session_id=? AND user_id=? AND position=?",(r["id"],me,pos)).fetchone():break
        c.execute("INSERT INTO trade_items(session_id,user_id,position,slime_key) VALUES(?,?,?,?)",(r["id"],me,pos,key))
        c.execute("UPDATE trade_sessions SET initiator_yes=FALSE,target_yes=FALSE,updated_at=? WHERE id=?",(time.time(),r["id"]));c.commit()
    finally:c.close()
    await board(legacy,msg,str(r["id"]),bot,f"Добавлен: {info.get('Name',key)}")

async def remove(legacy,msg,bot,arg):
    me=int(msg.from_user.id);r=active(me)
    if not r:await msg.answer("Сейчас у вас нет активного трейда.");return
    c=connect()
    try:
        if arg.isdigit():
            it=c.execute("SELECT position,slime_key FROM trade_items WHERE session_id=? AND user_id=? AND position=?",(r["id"],me,int(arg)-1)).fetchone()
        else:
            s=slime(legacy,arg);key=s[0] if s else ""
            it=c.execute("SELECT position,slime_key FROM trade_items WHERE session_id=? AND user_id=? AND slime_key=? ORDER BY position LIMIT 1",(r["id"],me,key)).fetchone()
        if not it:await msg.answer("Такого слайма в вашем трейде нет.");return
        c.execute("DELETE FROM trade_items WHERE session_id=? AND user_id=? AND position=?",(r["id"],me,int(it["position"])))
        c.execute("UPDATE trade_sessions SET initiator_yes=FALSE,target_yes=FALSE,status='active',updated_at=? WHERE id=?",(time.time(),r["id"]));c.commit()
    finally:c.close()
    await board(legacy,msg,str(r["id"]),bot,"Слайм убран из трейда.")

async def vote(legacy,msg,bot,yes):
    me=int(msg.from_user.id);r=active(me)
    if not r or str(r["status"]) not in {"active","voting"}:await msg.answer("Сейчас нет трейда для подтверждения.");return
    sid=str(r["id"]);c=connect()
    try:
        if not yes:
            c.execute("UPDATE trade_sessions SET status='active',initiator_yes=FALSE,target_yes=FALSE,updated_at=? WHERE id=?",(time.time(),sid));c.commit()
            await msg.answer("Трейд отклонён. Состав можно изменить и подтвердить снова.");return
        c.execute("UPDATE trade_sessions SET status='voting',initiator_yes=CASE WHEN initiator_id=? THEN TRUE ELSE initiator_yes END,target_yes=CASE WHEN target_id=? THEN TRUE ELSE target_yes END,updated_at=? WHERE id=?",(me,me,time.time(),sid));c.commit()
    finally:c.close()
    r=session(sid)
    if r and bool(r["initiator_yes"]) and bool(r["target_yes"]):
        left,right=int(r["initiator_id"]),int(r["target_id"]);li,ri=items(sid,left),items(sid,right)
        c=connect()
        try:
            for uid,other,its in ((left,right,li),(right,left,ri)):
                for it in its:
                    if not owned(legacy,uid,str(it["slime_key"])):c.rollback();await msg.answer("Трейд отменён: один из слаймов уже отсутствует.");return
            b1=c.execute("SELECT new_bucks FROM users WHERE user_id=? FOR UPDATE",(left,)).fetchone();b2=c.execute("SELECT new_bucks FROM users WHERE user_id=? FOR UPDATE",(right,)).fetchone()
            if not b1 or not b2 or int(b1["new_bucks"])<FEE or int(b2["new_bucks"])<FEE:
                c.rollback();await msg.answer(f"У каждого должно быть минимум {FEE} баксов.");return
            for uid,other,its in ((left,right,li),(right,left,ri)):
                for it in its:
                    key=str(it["slime_key"]);inst=c.execute("SELECT id FROM slime_instances WHERE user_id=? AND slime_key=? ORDER BY id LIMIT 1",(uid,key)).fetchone()
                    if not inst:c.rollback();await msg.answer("Трейд отменён: экземпляр слайма не найден.");return
                    c.execute("UPDATE slime_instances SET user_id=? WHERE id=?",(other,int(inst["id"])))
                    c.execute("UPDATE collections SET count=count-1 WHERE user_id=? AND slime_key=?",(uid,key))
                    c.execute("""INSERT INTO collections(user_id,slime_key,count) VALUES(?,?,1)
                        ON CONFLICT(user_id,slime_key) DO UPDATE SET count=collections.count+1""",(other,key))
            c.execute("DELETE FROM collections WHERE count<=0 AND user_id IN (?,?)",(left,right))
            c.execute("UPDATE users SET new_bucks=new_bucks-?,updated_at=? WHERE user_id IN (?,?)",(FEE,time.time(),left,right))
            c.execute("UPDATE trade_sessions SET status='completed',updated_at=? WHERE id=?",(time.time(),sid));c.commit()
        finally:c.close()
        await msg.answer("Трейд завершён. С обоих сторон списано по 75 баксов.")
        try:await bot.send_message(opponent(r,me),"Трейд завершён. С обоих сторон списано по 75 баксов.")
        except Exception:pass
    else:await msg.answer("Ваше подтверждение принято. Ждём вторую сторону.")

async def handle_message(legacy,msg):
    schema();n,a=cmd(msg.text or "");bot=getattr(getattr(legacy,"bot",None),"_bot",None)
    if not bot:return False
    if n in TRADE:
        if not a:await msg.answer("Использование: /trade @username");return True
        if a[0].lower() in ADD and len(a)>1:await add(legacy,msg,bot," ".join(a[1:]))
        elif a[0].lower() in REMOVE and len(a)>1:await remove(legacy,msg,bot," ".join(a[1:]))
        elif a[0].lower() in CLEAR:
            r=active(int(msg.from_user.id))
            if r:
                c=connect()
                try:c.execute("DELETE FROM trade_items WHERE session_id=? AND user_id=?",(r["id"],msg.from_user.id));c.execute("UPDATE trade_sessions SET initiator_yes=FALSE,target_yes=FALSE,updated_at=? WHERE id=?",(time.time(),r["id"]));c.commit()
                finally:c.close()
                await board(legacy,msg,str(r["id"]),bot,"Ваши клетки очищены.")
        else:await start(legacy,msg,bot,a[0])
        return True
    if n in ACCEPT:await accept(legacy,msg,bot);return True
    if n in ADD and a:await add(legacy,msg,bot," ".join(a));return True
    if n in REMOVE and a:await remove(legacy,msg,bot," ".join(a));return True
    if n in CLEAR:
        r=active(int(msg.from_user.id))
        if r:
            c=connect()
            try:c.execute("DELETE FROM trade_items WHERE session_id=? AND user_id=?",(r["id"],msg.from_user.id));c.commit()
            finally:c.close()
            await board(legacy,msg,str(r["id"]),bot,"Ваши клетки очищены.")
        return True
    if n in YES:await vote(legacy,msg,bot,True);return True
    if n in NO:await vote(legacy,msg,bot,False);return True
    return False
