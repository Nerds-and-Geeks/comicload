-- A dump shaped the way mysqldump actually writes one: the INSERTs carry no column
-- list, the tables are wider than comicload needs, and the columns comicload wants are
-- not the leading ones. A positional loader reads `format` as `publisher_id` here.
DROP TABLE IF EXISTS `gcd_publisher`;
CREATE TABLE `gcd_publisher` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `country_id` int(11) NOT NULL,
  `year_began` int(11) DEFAULT NULL,
  `notes` longtext,
  PRIMARY KEY (`id`),
  KEY `publisher_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `gcd_publisher` VALUES (1,'Marvel',225,1939,NULL),(2,'Image Comics',225,1992,NULL);

DROP TABLE IF EXISTS `gcd_series`;
CREATE TABLE `gcd_series` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `format` varchar(255) DEFAULT NULL,
  `year_began` int(11) DEFAULT NULL,
  `publication_dates` varchar(255) DEFAULT NULL,
  `publisher_id` int(11) DEFAULT NULL,
  `issue_count` int(11) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `series_publisher` (`publisher_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `gcd_series` VALUES (10,'The Punisher','color; 32 pgs',2000,'2000-2004',1,37),(11,'Alex + Ada','color; 32 pgs',2013,'2013-2015',2,15);

DROP TABLE IF EXISTS `gcd_issue`;
CREATE TABLE `gcd_issue` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `number` varchar(50) NOT NULL,
  `volume` varchar(50) DEFAULT NULL,
  `series_id` int(11) NOT NULL,
  `indicia_publisher_id` int(11) DEFAULT NULL,
  `price` varchar(255) DEFAULT NULL,
  `on_sale_date` varchar(10) DEFAULT NULL,
  `barcode` varchar(38) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `issue_barcode` (`barcode`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `gcd_issue` VALUES (100,'12','1',10,5,'2.99','2001-03-01','75960608457000111'),(101,'2','1',11,6,'3.50','2013-12-11','70985301491000211');
