"""Thread-owned OpenGL compositing with all sixteen blend modes on Windows."""
import os
import numpy as np
from PIL import Image

VERTEX = '''#version 330
in vec2 in_pos;
out vec2 uv;
void main() { uv = (in_pos+1.0)*0.5; gl_Position=vec4(in_pos,0,1); }
'''
FRAGMENT = '''#version 330
uniform sampler2D back;
uniform sampler2D source;
uniform int mode;
in vec2 uv;
out vec4 color;
void main() {
 vec4 cb=texture(back,uv), cs=texture(source,uv);
 vec3 b=cb.rgb,s=cs.rgb,v=s;
 if(mode==1) v=b*s;
 if(mode==2) v=b+s-b*s;
 if(mode==3) v=mix(2*b*s,1-2*(1-b)*(1-s),step(vec3(.5),b));
 if(mode==4) v=min(b,s);
 if(mode==5) v=max(b,s);
 if(mode==6) v=abs(b-s);
 if(mode==7) v=b+s-2*b*s;
 if(mode==8) v=mix(min(vec3(1),b/max(1-s,vec3(1e-7))),vec3(0),equal(b,vec3(0)));
 if(mode==9) v=mix(1-min(vec3(1),(1-b)/max(s,vec3(1e-7))),vec3(1),greaterThanEqual(b,vec3(1)));
 if(mode==10) v=max(vec3(0),b+s-1);
 if(mode==11) v=min(vec3(1),b+s);
 if(mode==12) v=mix(2*b*s,1-2*(1-b)*(1-s),step(vec3(.5),s));
 if(mode==13) {
   vec3 d=mix(((16*b-12)*b+4)*b,sqrt(b),greaterThan(b,vec3(.25)));
   v=mix(b-(1-2*s)*b*(1-b),b+(2*s-1)*(d-b),greaterThan(s,vec3(.5)));
 }
 if(mode==14) v=max(vec3(0),b-s);
 if(mode==15) v=min(vec3(1),b/max(s,vec3(1e-7)));
 float a=cs.a+cb.a*(1-cs.a);
 vec3 rgb=((1-cs.a)*cb.a*b+(1-cb.a)*cs.a*s+cb.a*cs.a*v)/max(a,1e-7);
 color=vec4(rgb,a);
}
'''
PLACE = '''#version 330
uniform sampler2D source;
uniform vec3 row0;
uniform vec3 row1;
uniform vec2 output_size;
uniform vec2 source_size;
in vec2 uv;
out vec4 color;
void main() {
 vec3 p=vec3(uv*output_size,1);
 vec2 q=vec2(dot(row0,p),dot(row1,p))/source_size;
 color=(q.x<0 || q.x>1 || q.y<0 || q.y>1) ? vec4(0) : texture(source,q);
}
'''


class GPUBackend:
    def __init__(self):
        import moderngl
        self.ctx = moderngl.create_context(standalone=True, require=330)
        self.name = self.ctx.info.get("GL_RENDERER", "OpenGL")
        self.program = self.ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        self.place_program = self.ctx.program(vertex_shader=VERTEX, fragment_shader=PLACE)
        self.vertices = self.ctx.buffer(np.float32([[-1,-1],[1,-1],[-1,1],[1,1]]).tobytes())
        self.vao = self.ctx.simple_vertex_array(self.program, self.vertices, "in_pos")
        self.place_vao = self.ctx.simple_vertex_array(self.place_program, self.vertices, "in_pos")
        self.count = 0
        self.closed = False

    def _run(self, program, vao, textures, size):
        import moderngl
        output = self.ctx.texture(size, 4, dtype="f1")
        fbo = self.ctx.framebuffer(color_attachments=[output])
        try:
            fbo.use()
            self.ctx.viewport = (0, 0, *size)
            for unit, texture in enumerate(textures): texture.use(unit)
            vao.render(moderngl.TRIANGLE_STRIP)
            pixels = fbo.read(components=4, alignment=1)
            self.count += 1
            return Image.frombytes("RGBA", size, pixels)
        finally:
            fbo.release(); output.release()
            for texture in textures: texture.release()

    def blend(self, back, source, mode="Normal"):
        from engine import BLEND_MODES
        self.program["back"].value = 0
        self.program["source"].value = 1
        self.program["mode"].value = BLEND_MODES.index(mode)
        textures = [self.ctx.texture(im.size, 4, im.convert("RGBA").tobytes(), alignment=1) for im in (back, source)]
        return self._run(self.program, self.vao, textures, back.size)

    def close(self):
        if self.closed: return
        self.closed = True
        for resource in (self.vao, self.place_vao, self.vertices, self.program, self.place_program, self.ctx): resource.release()


def create_backend():
    if os.environ.get("COMPOSITOR_GPU", "auto").lower() in ("off", "cpu", "0"): return None, "CPU（手动关闭 GPU）"
    backend=None
    try:
        backend = GPUBackend()
        # Check a real draw rather than treating an installed driver as working GPU support.
        sample = backend.blend(Image.new("RGBA", (2, 2)), Image.new("RGBA", (2, 2), (31, 71, 91, 255)))
        if sample.getpixel((0, 0)) != (31, 71, 91, 255): raise RuntimeError("GPU 校验失败")
        return backend, "GPU: "+backend.name
    except Exception as error:
        if backend: backend.close()
        return None, "CPU（GPU 不可用："+str(error)+"）"
