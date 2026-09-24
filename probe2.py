import socket, struct, time
def send(s, tag, text):
    d = text.encode()+b"\x00"; s.sendall(struct.pack("<Ii", len(d), tag)+d)
def recv_all(s, t=1.5):
    s.settimeout(t); buf=b""
    try:
        while True:
            c=s.recv(65536)
            if not c: break
            buf+=c
    except socket.timeout: pass
    return buf
s=socket.create_connection(("127.0.0.1",4318))
tests=[(4,"APP:"),(4,"APP2:"),(4,"IDENT:"),(3,"APP:"),(1,"APP:"),
       (3,"CMD:65535:1+1"),(4,"CMD:65535:1+1"),(1,"CMD:65535:1+1"),(0,"CMD:65535:1+1"),(2,"CMD:65535:1+1"),(5,"CMD:65535:1+1"),
       (3,"CMD:65535:0:1+1"),(3,"CMD:1:0:1+1")]
for tag,t in tests:
    send(s,tag,t); print(tag,t,"->",recv_all(s,1.2))
