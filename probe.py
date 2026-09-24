import socket, struct, sys, time
def send(s, tag, text):
    d = text.encode()+b"\x00"; s.sendall(struct.pack("<Ii", len(d), tag)+d)
def recv_all(s, t=1.5):
    s.settimeout(t); out=[]; buf=b""
    try:
        while True:
            c = s.recv(65536)
            if not c: break
            buf += c
    except socket.timeout: pass
    while len(buf)>=8:
        n,tag = struct.unpack("<Ii", buf[:8])
        if len(buf)<8+n: break
        out.append((tag, buf[8:8+n])); buf=buf[8+n:]
    return out
s=socket.create_connection(("127.0.0.1",4318))
print(recv_all(s,1))
send(s,4,"APP:"); print(recv_all(s))
send(s,4,"LSQ:"); r=recv_all(s); print(r)
for cmd in sys.argv[1:]:
    send(s,3,cmd); print(cmd, "->", recv_all(s,2))
