import os
import re
import threading
import pycurl
import xml.etree.ElementTree as ET
from xml.dom.minidom import parseString


# Get the size of the target file and calculate file part size.
class Downloader:
    def __init__(self, url, parts):
        self.url = url
        # Extract the filename from the URL.
        self.fileName = re.search(r"(?:[^/][\d\w.]+)+$", self.url, flags=re.IGNORECASE).group(0)
        self.parts = parts
        self.fileSize = round(self._getSize())
        self.partSize = round(self.fileSize / self.parts)
        self.threads = dict()
        self.partPosition = dict()
        self.partState = dict()
        self.stop = False
        self.resume = False
        # Get XML root
        tree = ET.parse("state.xml")
        self.root = tree.getroot()

    # Get file size by only downloading the HEADER and then calling getinfo() for the length.
    def _getSize(self):
        curl = pycurl.Curl()
        curl.setopt(curl.URL, self.url)
        curl.setopt(curl.FOLLOWLOCATION, True)
        curl.setopt(curl.NOBODY, True)
        curl.perform()
        fileSize = curl.getinfo(curl.CONTENT_LENGTH_DOWNLOAD)
        curl.close()
        return fileSize

    # Track individual file part download progress, return non-zero value if stop flag is set to interrupt perform(),
    # manage state of each part.
    def _trackProgress(self, totalDown, currentDown, totalUp, currentUp):
        part = self.threads[threading.get_native_id()]
        # Add the current byte position of each individual part to a dictionary for writing in state.xml file.
        self.partPosition[part] = currentDown

        if currentDown != 0 and currentDown == totalDown:
            self.save(part, "complete")

        if self.stop:
            return 1

    # Calculate the part size, execute _downloadRange() in separate threads, merge file parts on download completion.
    def download(self):
        parts = self.parts
        partStart = 0
        partEnd = self.partSize
        threads = list()

        if not self.resume:
            for part in range(1, parts + 1):
                t = threading.Thread(target=self._downloadRange, args=(partStart, partEnd, part))
                threads.append(t)
                t.start()
                # Increment partStart by 1 for the first range before adding it with partSize else only add the
                # partSize and add partSize to partEnd for changing the end of range.
                partStart += self.partSize + 1 if part == 1 else self.partSize
                partEnd += self.partSize
        else:
            for file in self.root.findall("file"):
                if file.get("name") == self.fileName:
                    for part in file:
                        if part.get("state") == "incomplete":
                            partStart = re.search(r"^\d+", part.text).group(0)
                            partEnd = re.search(r"\d+$", part.text).group(0)
                            part = int(part.get("id"))
                            t = threading.Thread(target=self._downloadRange, args=(partStart, partEnd, part))
                            threads.append(t)
                            t.start()

        # Call join() on all threads so that _mergeFiles() is only called after thread execution is completed.
        for t in threads:
            t.join()

        # Only call _mergeFiles() if stop flag is set to false, so that it isn't called when pausing/stopping
        # the download.
        if not self.stop:
            self._mergeFiles(self.fileName, parts)

    # Download the specified range and write it to a file part, stop download if any exception is caught.
    def _downloadRange(self, startRange, endRange, fileNo):
        # Get uniquely assigned ID of the current thread by the kernel to identify file part.
        self.threads[threading.get_native_id()] = fileNo
        path = f"{self.fileName}{fileNo}.part"
        try:
            # Append bytes to file if it exists (resuming) else only write.
            with open(path, "ab" if os.path.exists(path) else "wb") as f:
                curl = pycurl.Curl()
                curl.setopt(curl.URL, self.url)
                curl.setopt(curl.FOLLOWLOCATION, True)
                curl.setopt(curl.RANGE, f"{startRange}-{endRange}")
                curl.setopt(curl.WRITEDATA, f)
                curl.setopt(curl.NOPROGRESS, False)
                curl.setopt(curl.XFERINFOFUNCTION, self._trackProgress)
                curl.perform()
                curl.close()
        except pycurl.error:
            self.save(fileNo, "incomplete")

    # Merge the file parts into one and delete the parts.
    def _mergeFiles(self, fileName, parts):
        with open(fileName, "wb") as o:
            for part in range(1, parts + 1):
                with open(f"{self.fileName}{part}.part", "rb") as p:
                    o.write(p.read())
                os.remove(f"{self.fileName}{part}.part")

    # Set stop flag to interrupt perform() execution.
    def interrupt(self):
        self.stop = True
        self.resume = False

    # Set restart flag and call download() to resume download from previous byte position.
    def reinstate(self):
        self.stop = False
        self.resume = True
        self.download()

    # Write part position to state.xml file.
    def save(self, part, state):
        # Only decrement parts counter if that specific part's state hasn't been added to partState
        # so that it isn't called in succession after download complete because _trackProgress()
        # might be called several times even after completion.
        if part not in self.partState.keys():
            self.parts -= 1
            self.partState[part] = state

        if not self.parts:
            data = ET.Element("data")
            file = ET.SubElement(data, "file")
            file.set("name", self.fileName)

            for part in self.partPosition.keys():
                item = ET.SubElement(file, "part")
                item.set("id", str(part))
                item.set("state", self.partState[part])
                # Multiply position with part - 1 to show the total Size for individual part then add the byte position
                # to partStart, add 1 to partStart for all starting ranges other than the first one.
                partStart = (self.partSize * (part - 1)) + self.partPosition[part]
                partStart += 1 if part != 1 else 0
                partEnd = self.partSize * part

                item.text = str(f"{partStart}-{partEnd}")
            # TODO Implement writing to state.xml for multiple files, make resumes edit the individual file tags.
            with open("state.xml", "w") as state:
                state.write(parseString(ET.tostring(data, encoding="unicode")).toprettyxml())
