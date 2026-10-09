"""Single background I/O lane; Tk callbacks execute only in Editor.poll_job."""
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor


@dataclass
class Job:
    label: str
    future: object
    on_success: object
    lock_document: bool
    cancellable: bool
    cancelled: bool = False


class JobRunner:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="document-job")
        self.current = None

    def start(self,label,function,args,on_success,lock_document=False,cancellable=True):
        if self.current: raise ValueError("请等待当前操作完成。")
        self.current = Job(label,self.executor.submit(function,*args),on_success,lock_document,cancellable)
        return self.current

    def cancel(self):
        if self.current and self.current.cancellable:
            self.current.cancelled = True
            self.current.future.cancel()

    def shutdown(self):
        self.executor.shutdown(wait=True,cancel_futures=True)


def filtered_document(document,layer_id,kind,value):
    from engine import filter_image
    layer = next(l for l in document.layers if l.id==layer_id)
    layer.image = filter_image(layer.image,kind,value)
    layer.raster_changed()
    return document


def keyed_document(document,layer_id,color,tolerance):
    from engine import color_mask
    color_mask(next(l for l in document.layers if l.id==layer_id),color,tolerance)
    return document


def segmented_document(document,layer_id,segmenter):
    from segmentation import apply_subject_mask
    layer = next(l for l in document.layers if l.id==layer_id)
    apply_subject_mask(layer,segmenter.mask(layer.image))
    return document,segmenter.provider
