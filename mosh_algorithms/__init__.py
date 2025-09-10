from .gop_iframe_drop import process as gop_iframe_drop
from .flow_leaky import process as flow_leaky
from .blockmatch_basic import process as blockmatch_basic
from .inspect_gop import process as inspect_gop  # NEW
from .gop_multi_drop_concat import process as gop_multi_drop_concat 
from .ui_keyframe_editor import process as ui_keyframe_editor
from .video_to_image_mosh import process as video_to_image_mosh 
from .image_to_video_mosh import process as image_to_video_mosh 

ALGORITHMS = {
    "gop_iframe_drop": gop_iframe_drop,
    "flow_leaky": flow_leaky,
    "blockmatch_basic": blockmatch_basic,
    "inspect_gop": inspect_gop,
    "gop_multi_drop_concat": gop_multi_drop_concat, 
    "ui_keyframe_editor": ui_keyframe_editor,
    "video_to_image_mosh": video_to_image_mosh,
    "image_to_video_mosh": image_to_video_mosh,
}
