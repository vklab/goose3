# -*- coding: utf-8 -*-
"""\
This is a python port of "Goose" orignialy licensed to Gravity.com
under one or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.

Python port was written by Xavier Grangier for Recrutae

Gravity.com licenses this file
to you under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import re
import os
from urlparse import urlparse, urljoin
from goose.utils import FileHelper
from goose.parsers import Parser
from goose.images.image import Image
from goose.images.utils import ImageUtils

KNOWN_IMG_DOM_NAMES = [
    "yn-story-related-media",
    "cnn_strylccimg300cntr",
    "big_photo",
    "ap-smallphoto-a",
]


class DepthTraversal(object):

    def __init__(self, node, parent_depth, sibling_depth):
        self.node = node
        self.parent_depth = parent_depth
        self.sibling_depth = sibling_depth


class ImageExtractor(object):
    pass


class UpgradedImageIExtractor(ImageExtractor):

    def __init__(self, http_client, article, config):
        self.custom_site_mapping = {}
        self.load_customesite_mapping()

        # article
        self.article = article

        # config
        self.config = config

        # What's the minimum bytes for an image we'd accept is
        self.images_min_bytes = 4000

        # the webpage url that we're extracting content from
        self.target_url = article.final_url

        # stores a hash of our url for
        # reference and image processing
        self.link_hash = article.link_hash

        # this lists all the known bad button names that we have
        self.badimages_names_re = re.compile(
            ".html|.gif|.ico|button|twitter.jpg|facebook.jpg|ap_buy_photo"
            "|digg.jpg|digg.png|delicious.png|facebook.png|reddit.jpg"
            "|doubleclick|diggthis|diggThis|adserver|/ads/|ec.atdmt.com"
            "|mediaplex.com|adsatt|view.atdmt"
        )

    def get_best_image(self, doc, topNode):
        image = self.check_known_elements()
        if image:
            return image

        image = self.check_large_images(topNode, 0, 0)
        if image:
            return image

        image = self.check_meta_tag()
        if image:
            return image
        return Image()

    def check_meta_tag(self):
        image = self.check_link_tag()
        if image:
            return image
        image = self.check_opengraph_tag()
        if image:
            return image


    def check_large_images(self, node, parent_depth_level, sibling_depth_level):
        """\
        although slow the best way to determine the best image is to download
        them and check the actual dimensions of the image when on disk
        so we'll go through a phased approach...
        1. get a list of ALL images from the parent node
        2. filter out any bad image names that we know of (gifs, ads, etc..)
        3. do a head request on each file to make sure it meets
           our bare requirements
        4. any images left over let's do a full GET request,
           download em to disk and check their dimensions
        5. Score images based on different factors like height/width
           and possibly things like color density
        """
        good_images = self.get_image_candidates(node)

        if good_images:
            scored_images = self.fetch_images(good_images, parent_depth_level)
            if scored_images:
                highscore_image = sorted(scored_images.items(),
                                        key=lambda x: x[1], reverse=True)[0][0]
                main_image = Image()
                main_image.src = highscore_image.src
                main_image.extraction_type = "bigimage"
                main_image.confidence_score = 100 / len(scored_images) \
                                    if len(scored_images) > 0 else 0
                return main_image

        depth_obj = self.get_depth_level(node, parent_depth_level, sibling_depth_level)
        if depth_obj:
            return self.check_large_images(depth_obj.node,
                            depth_obj.parent_depth, depth_obj.sibling_depth)

        return None

    def get_depth_level(self, node, parent_depth, sibling_depth):
        MAX_PARENT_DEPTH = 2
        if parent_depth > MAX_PARENT_DEPTH:
            return None
        else:
            sibling_node = Parser.previousSibling(node)
            if sibling_node is not None:
                return DepthTraversal(sibling_node, parent_depth, sibling_depth + 1)
            elif node is not None:
                parent = Parser.getParent(node)
                if parent is not None:
                    return DepthTraversal(parent, parent_depth + 1, 0)
        return None

    def fetch_images(self, images, depth_level):
        """\
        download the images to temp disk and set their dimensions
        - we're going to score the images in the order in which
          they appear so images higher up will have more importance,
        - we'll count the area of the 1st image as a score
          of 1 and then calculate how much larger or small each image after it is
        - we'll also make sure to try and weed out banner
          type ad blocks that have big widths and small heights or vice versa
        - so if the image is 3rd found in the dom it's
          sequence score would be 1 / 3 = .33 * diff
          in area from the first image
        """
        image_results = {}
        initial_area = float(0.0)
        total_score = float(0.0)
        cnt = float(1.0)
        MIN_WIDTH = 50
        for image in images[:30]:
            src = Parser.getAttribute(image, attr='src')
            src = self.build_image_path(src)
            local_image = self.get_local_image(src)
            width = local_image.width
            height = local_image.height
            src = local_image.src
            file_extension = local_image.file_extension

            if file_extension != '.gif' or file_extension != 'NA':
                if (depth_level >= 1 and local_image.width > 300) or depth_level < 1:
                    if not self.is_banner_dimensions(width, height):
                        if width > MIN_WIDTH:
                            sequence_score = float(1.0 / cnt)
                            area = float(width * height)
                            total_score = float(0.0)

                            if initial_area == 0:
                                initial_area = area * float(1.48)
                                total_score = 1
                            else:
                                area_difference = float(area / initial_area)
                                total_score = sequence_score * area_difference

                            image_results.update({local_image: total_score})
                            cnt += 1
                            cnt += 1
        return image_results

    def get_images(self):
        return None

    def is_banner_dimensions(self, width, height):
        """\
        returns true if we think this is kind of a bannery dimension
        like 600 / 100 = 6 may be a fishy dimension for a good image
        """
        if width == height:
            return False

        if width > height:
            diff = float(width / height)
            if diff > 5:
                return True

        if height > width:
            diff = float(height / width)
            if diff > 5:
                return True

        return False

    def get_node_images(self, node):
        images = Parser.getElementsByTag(node, tag='img')
        if images is not None and len(images) < 1:
            return None
        return images

    def filter_bad_names(self, images):
        """\
        takes a list of image elements
        and filters out the ones with bad names
        """
        good_images = []
        for image in images:
            if self.is_valid_filename(image):
                good_images.append(image)
        return good_images if len(good_images) > 0 else None

    def is_valid_filename(self, imageNode):
        """\
        will check the image src against a list
        of bad image files we know of like buttons, etc...
        """
        src = Parser.getAttribute(imageNode, attr='src')

        if not src:
            return False

        if self.badimages_names_re.search(src):
            return False

        return True

    def get_image_candidates(self, node):
        good_images = []
        filtered_images = []
        images = self.get_node_images(node)
        if images:
            filtered_images = self.filter_bad_names(images)
        if filtered_images:
            good_images = self.get_images_bytesize_match(filtered_images)
        return good_images

    def get_images_bytesize_match(self, images):
        """\
        loop through all the images and find the ones
        that have the best bytez to even make them a candidate
        """
        cnt = 0
        MAX_BYTES_SIZE = 15728640
        good_images = []
        for image in images:
            if cnt > 30:
                return good_images
            src = Parser.getAttribute(image, attr='src')
            src = self.build_image_path(src)
            local_image = self.get_local_image(src)
            if local_image:
                bytes = local_image.bytes
                if (bytes == 0 or bytes > self.images_min_bytes) \
                        and bytes < MAX_BYTES_SIZE:
                    good_images.append(image)
                else:
                    images.remove(image)
            cnt += 1
        return good_images if len(good_images) > 0 else None

    def get_node(self, node):
        return node if node else None

    def check_link_tag(self):
        """\
        checks to see if we were able to
        find open link_src on this page
        """
        node = self.article.raw_doc
        meta = Parser.getElementsByTag(node, tag='link', attr='rel', value='image_src')
        for item in meta:
            href = Parser.getAttribute(item, attr='href')
            if href:
                main_image = Image()
                main_image.src = href
                main_image.extraction_type = "linktag"
                main_image.confidence_score = 100
                local_image = self.get_local_image(main_image.src)
                if local_image:
                    main_image.bytes = local_image.bytes
                    main_image.height = local_image.height
                    main_image.width = local_image.width
                    return main_image
        return None

    def check_opengraph_tag(self):
        """\
        checks to see if we were able to
        find open graph tags on this page
        """
        node = self.article.raw_doc
        meta = Parser.getElementsByTag(node, tag='meta', attr='property', value='og:image')
        for item in meta:
            href = Parser.getAttribute(item, attr='content')
            if href:
                main_image = Image()
                main_image.src = href
                main_image.extraction_type = "opengraph"
                main_image.confidence_score = 100
                local_image = self.get_local_image(main_image.src)
                if local_image:
                    main_image.bytes = local_image.bytes
                    main_image.height = local_image.height
                    main_image.width = local_image.width
                    return main_image
        return None

    def get_local_image(self, src):
        """\
        returns the bytes of the image file on disk
        """
        local_image = ImageUtils.store_image(None,
                                    self.link_hash, src, self.config)
        return local_image

    def get_clean_domain(self):
        return self.article.domain.replace('www.', '')

    def check_known_elements(self):
        """\
        in here we check for known image contains from sites
        we've checked out like yahoo, techcrunch, etc... that have
        * known  places to look for good images.
        * TODO: enable this to use a series of settings files
          so people can define what the image ids/classes
          are on specific sites
        """
        domain = self.get_clean_domain()
        if domain in self.custom_site_mapping.keys():
            classes = self.custom_site_mapping.get(domain).split('|')
            for classname in classes:
                KNOWN_IMG_DOM_NAMES.append(classname)

        known_image = None

        for known_name in KNOWN_IMG_DOM_NAMES:
            known = Parser.getElementById(self.article.raw_doc, known_name)
            if not known:
                known = Parser.getElementsByTag(self.article.raw_doc,
                                                attr='class', value=known_name)
                if known:
                    known = known[0]
            if known:
                main_image = Parser.getElementsByTag(known, tag='img')
                if main_image:
                    known_image = main_image[0]

        if known_image is not None:
            known_image_source = Parser.getAttribute(known_image, attr='src')
            main_image = Image()
            main_image.src = self.build_image_path(known_image_source)
            main_image.extraction_type = "known"
            main_image.confidence_score = 90
            local_image = self.get_local_image(main_image.src)
            if local_image:
                main_image.bytes = local_image.bytes
                main_image.height = local_image.height
                main_image.width = local_image.width

            return main_image

    def build_image_path(self, src):
        """\
        This method will take an image path and build
        out the absolute path to that image
        * using the initial url we crawled
          so we can find a link to the image
          if they use relative urls like ../myimage.jpg
        """
        o = urlparse(src)
        # we have a full url
        if o.hostname:
            return o.geturl()
        # we have a relative url
        return urljoin(self.target_url, src)

    def load_customesite_mapping(self):
        # TODO
        path = os.path.join('images', 'known-image-css.txt')
        data_file = FileHelper.loadResourceFile(path)
        lines = data_file.splitlines()
        for line in lines:
            domain, css = line.split('^')
            self.custom_site_mapping.update({domain: css})