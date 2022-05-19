# -*- coding: utf-8 -*-

# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import division
from cgitb import text
import os
import itertools
import pyaudio
import queue
from google.cloud import mediatranslation as media
import json
import cv2
from threading import Thread,Lock
from PIL import ImageFont, ImageDraw, Image
import numpy as np
import string

with open('configuration.json') as json_file:
    parameters = json.load(json_file)

# Audio recording parameters
RATE = int(parameters["samplerate"])
CHANNELS = int(parameters["channels"])
CHUNK = int(RATE/20)  
DEV = int(parameters["dev"])
LEN_SHOW = int(parameters["len_show"])
ORIG_LANGUAGE = parameters["origlanguage"]
DEST_LANGUAGE = parameters["destlanguage"]
CREDENTIALS = parameters["credentials"]
THRESHOLD = float(parameters["threshold"])

SpeechEventType = media.StreamingTranslateSpeechResponse.SpeechEventType

global print_buffer
read_write_lock = Lock()


print_buffer =[]
class MicrophoneStream:
    """Opens a recording stream as a generator yielding the audio chunks."""

    def __init__(self, rate, chunk, dev_index):
        self._rate = rate
        self._chunk = chunk
        self._dev_index = dev_index

        # Create a thread-safe buffer of audio data
        self._buff = queue.Queue()
        self.closed = True
        self.create_stream()

    def create_stream(self):
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            input_device_index=self._dev_index,
            format=pyaudio.paInt16,
            channels=1, rate=self._rate,
            input=True, frames_per_buffer=self._chunk,
            # Run the audio stream asynchronously to fill the buffer object.
            # This is necessary so that the input device's buffer doesn't
            # overflow while the calling thread makes network requests, etc.
            stream_callback=self._fill_buffer,
        )
        stream = self._audio_stream

        self.closed = False

        return self,stream


    def __exit__(self, type=None, value=None, traceback=None):
        print("Se ha cerrado el stream")
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self._buff.put(None)
        self._audio_interface.terminate()


    def _fill_buffer(self, in_data, frame_count, time_info, status_flags):
        """Continuously collect data from the audio stream, into the buffer."""
        self._buff.put(in_data)
        return None, pyaudio.paContinue

    def exit(self):
        self.__exit__()

    def generator(self):
        while not self.closed:
            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            chunk = self._buff.get()
            if chunk is None:
                return
            data = [chunk]

            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self._buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            yield b''.join(data)



def listen_print_loop(responses):
    """Iterates through server responses and prints them.

    The responses passed is a generator that will block until a response
    is provided by the server.
    """
    text_buffer = []
    global print_buffer

    # Variable to detect if there is more than one line
    for response in responses:
        # Once the transcription settles, the response contains the
        # END_OF_SINGLE_UTTERANCE event.
        # text_buffer is the buffer that stores all the text
        
        translation = ''

        result = response.result
        translation = result.text_translation_result.translation
        translation = translation.translate(str.maketrans('', '', string.punctuation))
        print("Transalation {}".format(translation))
        division = translation.split()
        if len(text_buffer)>0:
            j = 0
            for i in range(0,len(division)):
                
                # Itera cada palabra para ver si ya la tiene previamente en el buffer
                if division[i] in flatten(text_buffer) :
                    # En este indice comienza la nueva frase
                    j = i
                    
            text_buffer.append(division[j:])
            if len(division[j+1:])>0:
                read_write_lock.acquire()
                print("Division: {}".format(division[j+1:]))
                print_buffer.append(division[j+1:])
                read_write_lock.release()
        else:
            text_buffer.append(division)

                
def flatten(list2d):
    return list(itertools.chain(*list2d))

def do_translation_loop(dev_index, lang,client,speech_config,config,first_request):
        while True:
            stream =  MicrophoneStream(RATE, CHUNK, dev_index)
            while True:
                audio_generator = stream.generator()

                mic_requests = (media.StreamingTranslateSpeechRequest(
                    audio_content=content,
                    streaming_config=config)
                    for content in audio_generator)

                requests = itertools.chain(iter([first_request]), mic_requests)
                first_request = media.StreamingTranslateSpeechRequest(
                    streaming_config=config, audio_content=None)
                responses = client.streaming_translate_speech(requests)
                # Print the translation responses as they arrive
                listen_print_loop(responses)

                if responses == 0:
                    pass

def image_loop():
    global print_buffer

    capture = cv2.VideoCapture(0)
  
    fontpath = "/home/sistemas/ar-subs/Ubuntu-Regular.ttf"     
    font = ImageFont.truetype(fontpath, 32)
    
    while True:
        ret,frame = capture.read()
        print("Print buffer: {}".format(print_buffer))
        # font
        font = cv2.FONT_HERSHEY_TRIPLEX
        
        # org
        org = (50, 50)
        
        # fontScale
        fontScale = 1
        
        # Blue color in BGR
        color = (255, 0, 0)
        b,g,r,a = 0,255,0,0
        
        # Line thickness of 2 px
        thickness = 2
        text_show = flatten(print_buffer)
        text_show = ' '.join(text_show)

        img_pil = Image.fromarray(frame)
        draw = ImageDraw.Draw(img_pil)
        draw.text((50, 100), text_show,fill = (b, g, r, a))
        
        """
        frame = cv2.putText(frame, "camión", org, font, 
                   fontScale, color, thickness, cv2.LINE_AA)
        """
        frame = np.array(img_pil)
        # Display the resulting frame
        cv2.imshow('frame', frame)
        if len(print_buffer)>LEN_SHOW:
            read_write_lock.acquire()
            print_buffer =print_buffer[LEN_SHOW:]
            read_write_lock.release()
        
        
        # the 'q' button is set as the
        # quitting button you may use any
        # desired button of your choice
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    capture.release()

        


def main():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS
    print('Begin speaking...')

    client = media.SpeechTranslationServiceClient()

    speech_config = media.TranslateSpeechConfig(
        audio_encoding='linear16',
        source_language_code=ORIG_LANGUAGE,
        target_language_code=DEST_LANGUAGE,
        sample_rate_hertz = RATE)

    config = media.StreamingTranslateSpeechConfig(
        audio_config=speech_config, single_utterance=False)

    # The first request contains the configuration.
    # Note that audio_content is explicitly set to None.
    first_request = media.StreamingTranslateSpeechRequest(
        streaming_config=config, audio_content=None)


    sound_thread = Thread(target = do_translation_loop,args =(DEV, CHANNELS,client,speech_config,config,first_request))
    sound_thread.start()
    image_thread = Thread(target = image_loop)
    image_thread.start()


if __name__ == '__main__':
    main()
