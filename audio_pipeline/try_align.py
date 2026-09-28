import sys
import numpy as np
from pydub import AudioSegment
from audio_pipeline.forced_segmenter import ForcedAlignSegmenter
from audio_pipeline.segmenter import split_sentence_words

seg = AudioSegment.from_file(sys.argv[1]).set_channels(1)
audio = np.array(seg.get_array_of_samples(), dtype=np.float32) / (1 << (8 * seg.sample_width - 1))
words = split_sentence_words(sys.argv[2])

result = ForcedAlignSegmenter(seg.frame_rate).split(audio, len(words), words)
print(result.note())
for row in result.table(seg.frame_rate):
    print(row)