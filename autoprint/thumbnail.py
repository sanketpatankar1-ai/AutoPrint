"""Bounded print-sheet preview rendered in an isolated process."""
import io
import json
import math
import resource
import sys

if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CPU,(4,5))
    import pypdfium2 as pdfium
    from PIL import Image, ImageOps
    options=json.loads(sys.argv[2]) if len(sys.argv)>2 else {}
    with pdfium.PdfDocument(sys.stdin.buffer.read()) as pdf:
        start=int(sys.argv[1]) if len(sys.argv)>1 else 0
        indices=options.get("indices",list(range(len(pdf))))
        if start in indices:
            indices=indices[indices.index(start):]
        count=int(options.get("layout",1))
        dimensions={"A3":(297,420),"A4":(210,297),"A5":(148,210),"Letter":(216,279),"Legal":(216,356)}
        paper=str(options.get("paper") or "A4")
        w,h=dimensions.get(paper, dimensions["A4"])
        orient=int(options.get("orientation", 0))
        if orient==4 or (orient==0 and len(pdf)>0 and pdf[0].get_size()[0] > pdf[0].get_size()[1]):
            w,h=h,w
        width,height=round(w/max(w,h)*900),round(h/max(w,h)*900)
        sheet=Image.new("RGB",(width,height),"white")
        cols=math.ceil(math.sqrt(count))
        rows=math.ceil(count/cols)
        cellw,cellh=width//cols,height//rows
        for position,index in enumerate(indices[:count]):
            page=pdf[index]
            pw,ph=page.get_size()
            if not all(math.isfinite(n) and n>0 for n in (pw,ph)):
                raise ValueError("Invalid page dimensions")
            bitmap=page.render(scale=min(2,900/max(pw,ph)))
            img=bitmap.to_pil().convert("RGB")
            img.thumbnail((max(1,cellw-24),max(1,cellh-24)))
            if options.get("mode") in ("BW", "bw"):
                img=ImageOps.grayscale(img).convert("RGB")
            x=(position%cols)*cellw+(cellw-img.width)//2
            y=(position//cols)*cellh+(cellh-img.height)//2
            sheet.paste(img,(x,y))
            bitmap.close()
            page.close()
        sheet.save(sys.stdout.buffer,format="PNG")
