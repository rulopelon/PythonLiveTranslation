# -*- coding: utf-8 -*-

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
import time
import textwrap


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
TIME_WORD = float(parameters["time_word"])
fontpath = parameters["font"] 

SpeechEventType = media.StreamingTranslateSpeechResponse.SpeechEventType

global print_buffer
global instant_print

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
    global instant_print

    # Variable to detect if there is more than one line
    for response in responses:
        # Once the transcription settles, the response contains the
        # END_OF_SINGLE_UTTERANCE event.
        # text_buffer is the buffer that stores all the text
        
        translation = ''
        if (response.speech_event_type == SpeechEventType.END_OF_SINGLE_UTTERANCE):
            print_buffer = []
            instant_print = ""
            break
        

        result = response.result
        translation = result.text_translation_result.translation
        translation = translation.translate(str.maketrans('', '', string.punctuation))
        print("Translation {}".format(translation))
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
                print_buffer.append(division[j+1:])
                read_write_lock.release()
        else:
            text_buffer.append(division)

                
def flatten(list2d):

    #return list(itertools.chain(*list2d))
    flatlist = [item for sublist in list2d for item in sublist]
    return flatlist
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
    # Function to get the image from the video card and add the text to the frame
    global instant_print
    # Creating the object to get the frames
    capture = cv2.VideoCapture(0)
  

       
    font = ImageFont.truetype(fontpath, 20)
    
    while True:
        # Getting the frame from the video card
        ret,frame = capture.read()
        
        # Blue color in BGRA
        b,g,r,a = 0,255,0,0
        

        # Transforms the array to an Image object
        img_pil = Image.fromarray(frame)

        # Draws the desired text
        draw = ImageDraw.Draw(img_pil)
        
        # Splits the string into lines of 40 characters
        lines = textwrap.wrap(instant_print, width=40)
        y_text = 100
        
        # Draws each line on the image
        for line in lines:
            width, height = font.getsize(line)
            draw.text((50, y_text), line, font=font, fill=(b, g, r, a))
            y_text += height

        # Transforms the image object to an array
        frame = np.array(img_pil)
        # Display the resulting frame
        cv2.imshow('frame', frame)
        
        # the 'q' button is set as the
        # quitting button you may use any
        # desired button of your choice
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    capture.release()

def word_handler_loop():
    global instant_print
    global print_buffer
    instant_print = ""
    # Function that checks if the print buffer is large enough to be showed on the screen
    # also checks the time that a sentence has been on the screen, if the time exceeds,
    while True:
        # if there are things to show
        
        if len(print_buffer)>0:
            # Gets the text to show as a single list
            splitted = instant_print.split()

            for element in print_buffer[0]:
                splitted.append(element)

            instant_print = ' '.join(splitted)
    
            print_buffer.pop(0)

        if len(instant_print.split())>=LEN_SHOW:
            splitted = instant_print.split()
            splitted.pop(0)
            instant_print = ' '.join(splitted) 
            
            
        else:
            pass
  
def main():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS
    print('The translation begins')

    client = media.SpeechTranslationServiceClient()
    # Setting the configuration of the translator
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

    # Creating and starting all the threads
    # Thread for the sunds acquisition
    sound_thread = Thread(target = do_translation_loop,args =(DEV, CHANNELS,client,speech_config,config,first_request))
    sound_thread.start()
    # Thread for the image capture and modification
    image_thread = Thread(target = image_loop)
    image_thread.start()
    # Thread for the text handler
    handler_loop = Thread(target= word_handler_loop)
    handler_loop.start()


if __name__ == '__main__':
    main()
